# Summarizer(진료 요약 생성) 설계 문서

본 문서는 **AI 서버 입장**에서 요약(Summarizer) 처리 흐름과  
Spring ↔ AI 내부 API(`/internal/summarizes`)의 요청/응답 구조를 정의한다.

> 한 줄 흐름 기준  
> - 녹음 업로드 → Spring이 DB/스토리지 저장 + AI 서버에 STT 요청  
> - AI 서버 STT → Whisper 호출 후 결과 Spring에 반환, Spring이 DB에 저장  
> - 요약 생성 요청 → Spring이 **DB에서 STT 결과(transcript)를 꺼내서** AI 서버에 요약 요청  
> - AI 서버 요약 → GPT로 summary 생성 후 Spring에 반환, Spring이 DB·응답 처리  


---

## 1. Summarizer의 역할

Summarizer의 목적:

1. Spring으로부터 `consultationId`, `recordingId`, `transcript` 를 입력받는다.
2. transcript(STT 결과 텍스트)를 기반으로 LLM을 호출하여 환자용 요약 JSON을 생성한다.
3. `content`, `tags` 구조를 반환한다.
4. Spring이 이 결과를 `summary` 테이블에 반영한다.


---

## 2. 내부 API: `POST /internal/summarizes`

### 2.1 Request (Spring → AI 서버)

Spring은 STT가 완료된 녹음에 대해, DB에서 transcript를 조회한 뒤  
아래 형태로 AI 서버에 요약 생성을 요청한다.

```json
{
  "consultationId": "c-uuid-123",
  "recordingId": "r-uuid-def",
  "transcript": "요즘 속이 불편하고...언제부터 그러셨나요?...",
}
```

| 필드           | 타입   | 설명                                      | 필수 |
|----------------|--------|-------------------------------------------|------|
| consultationId | String | 요약을 생성할 진료 ID                    | Y    |
| recordingId    | String | 해당 진료의 녹음 ID                      | Y    |
| transcript     | String | STT 결과 전체 텍스트 (Spring이 DB에서 조회)| Y    |

> 중요한 점  
> - STT 결과(transcript)는 **Spring이 관리**한다.  
> - Summarizer는 **DB를 보지 않고**, 요청에 포함된 `transcript`만 사용한다.  
> - STT가 존재하지 않는 경우에는 Spring 단계에서 에러를 처리하고  
>   `/internal/summarizes` 자체를 호출하지 않는 것이 기본 흐름이다.

---

## 3. Summarizer 내부 입력 (LLM 호출용)

내부 Summarizer 함수는 실제로 **transcript**을 이용해 LLM을 호출한다.

예시 내부 구조(참고용):

```json
{
  "consultation_id": "c-uuid-123",
  "recording_id": "r-uuid-def",
  "transcript": "요즘 속이 불편하고...언제부터 그러셨나요?...",
  "meta": {
    "language": "ko"
  }
}
```

---

## 4. Summarizer 출력(JSON) 구조

LLM이 생성해야 하는 요약 JSON 구조는 아래와 같다.  
이 구조는 `summary.content` / `summary.tags` 에 그대로 저장된다  
(구체적인 JSON 스키마는 `summary_schema.json` 참조).

```json
{
  "content": {
    "patient_summary": "",
    "diagnosis": [],
    "prescriptions": [],
    "instructions": [],
    "meta": {
      "language": "ko",
      "version": "v1",
      "model": ""
    }
  },
  "tags": {
    "keywords": [],
    "risk_level": "LOW"
  }
}
```

### 필드 설명

- `content.patient_summary` :  
  환자가 이해하기 쉬운 한국어 진료 요약 (증상, 검사, 치료 계획 등)
- `content.diagnosis` :  
  추정/확정 진단명 목록
- `content.prescriptions` :  
  약 처방 리스트(name, dose, frequency 등)
- `content.instructions` :  
  환자 주의사항, 복약/생활 지침
- `content.meta.language` : `"ko"` 고정 (현 버전)
- `content.meta.version` : 요약 스키마/버전(예: `"v1"`)
- `content.meta.model` : 사용한 LLM 모델명 (예: `"gpt-4.1-mini"`)
- `tags.keywords` : 진료 관련 핵심 키워드
- `tags.risk_level` : `"LOW"`, `"MEDIUM"`, `"HIGH"` 중 하나

---

## 5. Success Response (AI → Spring)

내부 API 응답 전체 구조는 다음과 같다.

```json
{
  "status": 200,
  "message": "요약 생성이 완료되었습니다.",
  "data": {
    "consultationId": "c-uuid-123",
    "recordingId": "r-uuid-def",
    "content": {
      "patient_summary": "이번 진료에서는 감기 초기 증상으로 판단되어 해열제와 감기약을 3일 처방했습니다...",
      "diagnosis": ["상기도 감염", "감기 의심"],
      "prescriptions": [
        {
          "name": "타이레놀",
          "dose": "500mg",
          "frequency": "1일 3회, 3일"
        }
      ],
      "instructions": [
        "3일 이상 열이 지속될 경우 재내원 바랍니다.",
        "물 섭취와 휴식을 충분히 해 주세요."
      ],
      "meta": {
        "language": "ko",
        "version": "v1",
        "model": "gpt-4.1-mini"
      }
    },
    "tags": {
      "keywords": ["상기도 감염", "감기", "타이레놀"],
      "risk_level": "LOW"
    },
    "meta": {
      "provider": "openai",
      "processedAt": "2025-11-19T13:05:00Z"
    }
  }
}
```

- `data.content`, `data.tags` : LLM이 생성한 요약 JSON
- `data.meta.provider` : `"openai"`
- `data.meta.processedAt` : 요약 생성 완료 시각(ISO8601)

---


## 6. Error Response 정의

### 6.1 잘못된 요청 (400 Bad Request)

```json
{
  "status": 400,
  "message": "유효하지 않은 요청입니다.",
  "error": "BAD_REQUEST"
}
```

예:

- `transcript` 누락 또는 빈 문자열
- 필드 타입 불일치

### 6.2 내부 오류 (500 Internal Server Error)

```json
{
  "status": 500,
  "message": "요약 생성 중 오류가 발생했습니다.",
  "error": "SUMMARY_PROCESSING_FAILED"
}
```

예: OpenAI 호출 실패, JSON 파싱 실패, 프롬프트 오류 등.

> ⚠️ STT 결과 없음(예: STT_NOT_FOUND)은  
> AI 서버가 아니라 **Spring에서 처리해야 할 에러**이다.  
> STT가 준비되지 않은 상태에서는 `/internal/summarize`를 호출하지 않는 것이 원칙.

---

## 7. TODO / 확장 사항

- 환자/보호자에게 덜 충격적인 표현을 사용하는 “톤 조절” 규칙 추가
- 요약 길이 옵션 (짧게/보통/길게) 지원
- 다국어 요약(meta.language 활용)
- 고위험 진료(예: 암, 중환자)에 대한 risk_level 기준 세분화
