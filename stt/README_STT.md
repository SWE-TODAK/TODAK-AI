# STT(녹음 → 텍스트) 설계 문서

본 문서는 **AI 서버 입장**에서 STT 처리 흐름과  
Spring ↔ AI 내부 API(`/internal/transcriptions`)의 요청/응답 구조를 정의한다.

AI 서버는 **DB를 직접 수정하지 않고**,  
OpenAI STT를 호출해 **segment 기반 STT 결과(JSON)** 만 반환한다.  
DB 반영 및 status 변경은 **Spring** 이 담당한다.

---

## 1. STT의 역할

STT의 목적은 다음과 같다:

1. Spring으로부터 `recordingId`, `fileUrl` 을 JSON으로 입력받는다.
2. `fileUrl` 의 음성 파일을 다운로드한다.
3. OpenAI STT(Whisper 호스팅, `whisper-1` 등)를 호출한다.
4. 변환 결과를 segment 배열(timestamp + text) 형태로 정리한다.
5. duration, language, meta 정보와 함께 JSON으로 Spring에 반환한다.

---

## 2. 내부 API: `POST /internal/stt`

### 2.1 Request (Spring → AI 서버)

```json
{
  "recordingId": "r-uuid-def",
  "consultationId": "c-uuid-123",
  "fileUrl": "https://storage.todak.app/recordings/r-uuid-def.m4a",
  "language": "ko"
}
```

| 필드            | 타입   | 설명                                 | 필수 |
|-----------------|--------|--------------------------------------|------|
| recordingId     | String | STT 대상 녹음 ID (예: r-uuid-def)   | Y    |
| consultationId  | String | 해당 녹음이 속한 진료 ID            | Y    |
| fileUrl         | String | S3 등 저장된 음성 파일 URL          | Y    |
| language        | String | 음성 언어(ko, en 등). 기본 `"ko"`   | N    |

> `model` 정보는 요청에 포함하지 않는다.  
> 어떤 STT 모델을 사용할지는 AI 서버 내부 설정에서 관리한다.

---

## 3. STT 처리 및 응답 구조

### 3.1 OpenAI STT 호출 (내부 로직)

AI 서버는 다음과 같은 순서로 동작한다:

1. `fileUrl` 로부터 음성 파일 다운로드
2. OpenAI STT API 호출

   - 예시 (Python):

   ```python
   stt_resp = openai.audio.transcriptions.create(
       model="gpt-4o-mini-transcribe",  # 또는 whisper-1 등
       file=audio_file,
       response_format="json",
       language=language,
   )
   ```

3. OpenAI 응답의 텍스트/segments를 내부 포맷으로 변환

### 3.2 Success Response (AI → Spring)

```json
{
  "status": 200,
  "message": "STT 변환이 완료되었습니다.",
  "data": {
    "recordingId": "r-uuid-def",
    "consultationId": "c-uuid-123",
    "duration": 300,
    "language": "ko",
    "sttResult": [
      {
        "timestamp": "00:00:10",
        "text": "안녕하세요, 요즘 속이 불편해서 방문했습니다."
      },
      {
        "timestamp": "00:00:15",
        "text": "네, 언제부터 그러셨나요?"
      }
    ],
    "meta": {
      "provider": "openai",
      "model": "whisper-1",
      "processedAt": "2025-11-19T13:00:00Z"
    }
  }
}
```

#### 필드 설명

- `duration` : 전체 음성 길이(초). Whisper 결과 또는 ffprobe 등을 통해 계산.
- `language` : 인식된 언어 코드(예: `"ko"`).
- `sttResult` : segment 배열
  - `timestamp` : `"HH:MM:SS"` 문자열
  - `text` : 해당 구간의 인식 결과 텍스트
- `meta` :
  - `provider` : `"openai"`
  - `model` : 실제 사용한 STT 모델명 (예: `"whisper-1"`, `"gpt-4o-mini-transcribe"`)
  - `processedAt` : STT 처리 완료 시각(ISO8601)

> speaker(화자) 정보는 현재 응답에 포함하지 않는다.  
> 화자 분리는 차후 확장 기능으로 고려한다.

---

## 4. Spring 측 후처리 (참고)

AI 서버는 DB를 수정하지 않으며,  
Spring이 STT 결과를 받아 직접 DB에 반영한다.

### Spring에서 수행하는 작업 예시

1. `sttResult` 를 순회하며 전체 transcript 문자열 생성

   ```text
   "안녕하세요, 요즘 속이 불편해서 방문했습니다.\n네, 언제부터 그러셨나요?\n..."
   ```

2. `recordings` 테이블 업데이트

   - `transcript` ← 합쳐진 텍스트
   - `duration_seconds` ← `data.duration`
   - `status` ← `'TRANSCRIBED'`

3. 필요 시 `sttResult` raw 배열을 별도 컬럼/테이블에 JSON으로 저장 (선택)

---

## 5. Error Response 정의

### 5.1 잘못된 요청 (400 Bad Request)

```json
{
  "status": 400,
  "message": "유효하지 않은 요청입니다.",
  "error": "BAD_REQUEST"
}
```

예: 필수 필드 누락, fileUrl 형식 오류 등.

### 5.2 인증 실패 (401 Unauthorized)

```json
{
  "status": 401,
  "message": "내부 API 인증 실패",
  "error": "UNAUTHORIZED"
}
```

- `Authorization: Bearer <INTERNAL_API_KEY>` 가 유효하지 않은 경우

### 5.3 내부 오류 (500 Internal Server Error)

```json
{
  "status": 500,
  "message": "STT 처리 중 오류가 발생했습니다.",
  "error": "STT_PROCESSING_FAILED"
}
```

예: OpenAI STT 호출 실패, 파일 다운로드 실패, 예기치 못한 예외 등.

---

## 6. TODO / 확장 사항

- sttResult에 speaker 정보 추가 (화자 분리 기능)
- segment 기반 구간 재생 UI를 위한 추가 메타데이터
- 길이가 매우 긴 진료에 대한 chunk 단위 STT 및 병합 전략
- 다국어 STT 지원 (language 자동 감지 + 명시적 설정 혼합)
