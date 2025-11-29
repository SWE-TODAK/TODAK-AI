# STT(녹음 → 텍스트) 설계 문서 — 최신 버전

본 문서는 **AI 서버(FastAPI)** 관점에서  
STT 처리 흐름과 Spring ↔ AI 내부 API(`/internal/transcriptions`)의  
요청/응답 구조를 정의한다.

AI 서버는 **DB 접근을 하지 않으며**,  
Spring이 전달한 **음성 파일 자체**를 받아  
OpenAI Whisper STT를 호출한 뒤 **텍스트 결과만 JSON으로 반환**한다.

DB 반영 및 status 변경은 **Spring**이 수행한다.

---

# 1. STT의 역할

STT의 목적은 다음과 같다:

1. Spring이 전송한 `recordingId`, `consultationId`, `language`, `file(음성)` 을 입력받는다.
2. OpenAI Whisper(`whisper-1`) API로 음성 → 텍스트 변환.
3. 변환된 전체 텍스트를 단일 segment로 정리한다.
   - (현재 timestamp 없음)
4. duration, language, meta 정보를 포함해 JSON으로 Spring에 반환한다.

---

# 2. 내부 API: `POST /internal/transcriptions`

### 📌 요청 방식  
**multipart/form-data**

### 2.1 Request (Spring → AI 서버)

요청 필드:

| 필드            | 타입               | 설명                             | 필수 |
|-----------------|--------------------|----------------------------------|------|
| recordingId     | String             | STT 대상 녹음 ID                | Y    |
| consultationId  | String             | 해당 진료 ID                    | Y    |
| language        | String             | 기본 `"ko"`                     | N    |
| file            | Binary(File)       | Spring이 직접 전송한 음성 파일  | Y    |

### 2.2 예시 (multipart/form-data)

POST /internal/transcriptions
Content-Type: multipart/form-data
X-Internal-Key: todak-internal-xxx

recordingId = "r-uuid"
consultationId = "c-uuid"
language = "ko"
file = (binary wav/m4a/mp3...)


- `X-Internal-Key`: Spring ↔ AI 서버 내부 인증 전용 헤더
- 실제 구현에서는 `INTERNAL_API_KEY` 환경 변수와 비교하여 검증

---

# 3. STT 처리 및 응답 구조

## 3.1 OpenAI Whisper 호출 (내부 로직 개념)

AI 서버는 대략 다음 순서로 동작한다:

1. FastAPI `UploadFile` 로부터 파일 바이트를 읽는다.
2. OpenAI Whisper STT 호출:

```python
result = client.audio.transcriptions.create(
    model="whisper-1",
    file=(filename, data_bytes),
    language=language,
)
```
3. transcript 문자열과 함께 응답 JSON을 구성한다.
(현재는 별도의 segment 배열 없이, transcript만 반환)
---

## 3.2 success response (AI->Spring)
```json
{
  "status": 200,
  "message": "STT 변환이 완료되었습니다.",
  "data": {
    "recordingId": "r-uuid-def",
    "consultationId": "c-uuid-123",
    "duration": 118,
    "language": "ko",
    "transcript": "선생님, 예. 앉으세요. 엄마, 이쪽으로 앉아. 결과가 나왔는데요. CT상 혹이 보여서 MRI촬영을 하셨는데 안타깝게도 악성 가능성이 있어 보여서 조직검사로 확인해 봐야 돼요. ...",
    "meta": {
      "provider": "openai",
      "model": "whisper-1",
      "processedAt": "2025-11-29T14:01:07.537563+00:00"
    }
  }
}
```
### 필드 설명
- recordingId : STT가 수행된 녹음 ID
- consultationId : 녹음이 속한 진료 ID
- duration : 전체 음성 길이(초). (현재는 0 또는 추후 계산값)
- language : 인식된 언어 코드(예: "ko")
- transcript : Whisper가 생성한 전체 STT 텍스트 문자열
- meta.provider : "openai"
- meta.model : "whisper-1" 등 실제 사용한 STT 모델명
- meta.processedAt : STT 처리 완료 시각(ISO8601, UTC 기준)
---

## 4. Spring 측 후처리 (참고)

AI 서버는 DB를 수정하지 않으며,  
Spring이 STT 결과를 받아 직접 DB에 반영한다.

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
