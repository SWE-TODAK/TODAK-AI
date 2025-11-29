# AI Pipeline 개요 (STT + Summarizer)

본 문서는 **Spring 백엔드 ↔ AI 서버(FastAPI/Python)** 구조를 기준으로  
STT(음성 → 텍스트)와 Summarizer(텍스트 → 요약) 파이프라인을 정리한다.

AI 서버는 **DB에 직접 접근하지 않고**,  
오직 JSON 입력을 받아 OpenAI API를 호출한 뒤 **결과만 반환**하는  
“계산기 역할”만 담당한다.  
DB 반영은 모두 Spring에서 수행한다.

---

## 1. 전체 아키텍처

```
[모바일/웹 클라이언트]
        ↓
      (HTTP)
        ↓
[Spring 백엔드]  ──────────────→  [AI 서버 (FastAPI + Python)]
  - /consultations                 - /internal/transcriptions
  - /recordings                    - /internal/summarizes
  - /summaries
  - DB(PostgreSQL 등)
```

### 역할 분리

- **Spring 백엔드**
  - 인증/인가, 도메인 로직 담당
  - `consultations`, `recordings`, `summary` 테이블 관리
  - AI 서버에 내부 API 호출(`/internal/transcriptions`, `/internal/summarize`)
  - AI 서버 결과를 DB에 저장하고, 외부 공개 API 응답 생성

- **AI 서버 (Python, FastAPI)**
  - OpenAI STT(API) 호출 → Whisper 호스팅 기반 STT 수행
  - OpenAI Chat(API) 호출 → 요약(JSON) 생성
  - STT/요약 결과를 JSON으로 반환
  - DB, 세션, 인증은 관여하지 않음 (내부 API 키 인증만 사용)

---

## 2. 사용 DB 테이블 (Spring 관점)

### 2.1 consultations

- `consultation_id (PK)`
- `appointment_id (FK → appointments)`
- `hospital_id (FK → hospitals)`
- `patient_id (FK → users)`
- `started_at` : 진료 시작 시각(녹음 시작 시점)
- `recording_path` : 메인 녹음 파일 경로(선택적으로 사용)
- `summary` : 요약 JSON 캐시 (summary.content 저장 용도)
- `created_at`

### 2.2 recordings

- `recording_id (PK)`
- `consultation_id (FK → consultations)`
- `hospital_id (FK → hospitals)`
- `file_path` : 실제 음성 파일 URL(S3 등)
- `duration_seconds` : 녹음 길이(초)
- `file_size_mb`
- `format` : mp3, m4a, wav 등
- `transcript` : 전체 STT 텍스트 (Spring이 STT 결과를 합쳐서 저장)
- `status` :  
  - `WAITING` / `AUTHORIZED` / `UPLOADED` / `TRANSCRIBED` / `FAILED`
- `authorized_at`
- `created_at`

### 2.3 summary

- `summary_id (PK)`
- `consultation_id (FK → consultations)`
- `recording_id (FK → recordings)`
- `content` : 요약 본문(JSONB, patient_summary/diagnosis/... 포함)
- `tags` : 키워드, risk_level 등(JSONB)
- `created_at`

summary.content / tags 구조는 `ai/summarizer/summary_schema.json` 을 따른다.

---

## 3. STT 파이프라인 (Spring ↔ AI 서버)

### 3.1 내부 API: `POST /internal/transcriptions`

- 호출 주체: **Spring**
- 목적: 특정 녹음 파일에 대해 STT 수행

#### Request (Spring → AI)

- `Content-Type: multipart/form-data`

필드:

- `recordingId`: STT 대상 녹음 ID (문자열/UUID)
- `consultationId`: 해당 녹음이 속한 진료 ID
- `language`: `"ko"` (기본값), 필요 시 `"en"` 등
- `file`: 실제 음성 파일 (mp3, m4a, wav 등)

예시 (형식 설명용):

- recordingId = `"r-uuid-def"`
- consultationId = `"c-uuid-123"`
- language = `"ko"`
- file = `r-uuid-def.m4a` 바이너리

#### Success Response (AI → Spring)

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

- `sttResult`: **timestamp + text** 로 이루어진 segment 배열
- speaker 정보는 포함하지 않음 (화자 분리는 향후 확장)

#### Spring에서 할 일

AI 서버에서 받은 `sttResult` 를 기반으로:

1. **전체 transcript 문자열 생성**
   - 예: `"\n".join([seg.text for seg in sttResult])`
2. `recordings` 테이블 업데이트
   - `transcript` ← 합쳐진 텍스트
   - `duration_seconds` ← `data.duration`
   - `status` ← `'TRANSCRIBED'`
3. 필요시 STT raw segment JSON을 별도 컬럼/테이블에 저장(선택)

---

## 4. Summarizer 파이프라인 (Spring ↔ AI 서버)

### 4.1 내부 API: `POST /internal/summarizes`

- 호출 주체: **Spring**
- 목적: 특정 진료/녹음에 대해 요약 JSON 생성

#### Request (Spring → AI)

```json
{
  "consultationId": "c-uuid-123",
  "recordingId": "r-uuid-def",
  "transcript": "환자: 요즘 속이 불편하고...\n의사: 언제부터 그러셨나요?...",
}
```

- Spring은 내부적으로 `recordings.transcript` 를 이미 가지고 있으므로 AI 서버는 호출 시 transcript를 추가적으로 받게 함.

#### Success Response (AI → Spring)

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

- `content`, `tags` 구조는 외부 공개 API와 동일
- `meta.provider`, `meta.processedAt` 는 AI 서버에서 채워주는 메타정보

#### Spring에서 할 일

1. `summary` 테이블에 INSERT
   - `consultation_id` = `data.consultationId`
   - `recording_id` = `data.recordingId`
   - `content` = `data.content`
   - `tags` = `data.tags`
2. (선택) `consultations.summary` 캐시 업데이트
   - `summary` 컬럼에 `data.content` 저장
3. `POST /summaries/{consultationId}` 응답으로 그대로 내려주거나 일부만 노출

---

## 5. recordings.status 상태 정리 (Spring 기준)

| 상태          | 설명                                     | 비고                    |
|---------------|------------------------------------------|-------------------------|
| `WAITING`     | 진료 생성, 녹음 전                      | STT 대상 아님           |
| `AUTHORIZED`  | 녹음 동의 코드 인증 완료                |                         |
| `UPLOADED`    | 녹음 파일 업로드 완료                   | `POST /internal/transcriptions` 대상 |
| `TRANSCRIBED` | STT 완료, transcript 저장됨             | 요약 생성 가능 상태     |
| `FAILED`      | STT 실패                                | 재시도 후보             |

AI 서버는 status를 직접 변경하지 않고,  
Spring이 STT/요약 결과를 반영하면서 status를 갱신한다.

---

## 6. TODO / 확장 포인트

- STT segment를 이용한 “타임라인 기반 요약” 기능 (예: 구간별 요약)
- 화자 분리(speaker diarization) 추가
- 요약 다국어 지원 (meta.language 활용)
- 요약 버전 관리(meta.version) 정책 수립
