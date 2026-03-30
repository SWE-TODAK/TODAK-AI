from starlette.formparsers import MultiPartParser
MultiPartParser.max_part_size = 10 * 1024 * 1024   # 10MB
MultiPartParser.max_file_size = 50 * 1024 * 1024   # 50MB

from pydantic import BaseModel
from typing import Optional
from uuid import UUID

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    UploadFile,
    File,
    Form,
)

from config import INTERNAL_API_KEY
from stt.stt_service import run_stt, run_stt_from_url
from summarizer.summarizer_service import run_summary

# FastAPI 인스턴스
app = FastAPI(
    title="Todak AI Server",
    description="Internal STT + Summarizer API",
    version="1.0.0",
)

class TranscriptionByUrlRequest(BaseModel):
    recordingId: str
    language: str = "ko"
    audioUrl: str
    vadEnabled: bool = False
    maxSegmentSec: int = 25
    vadAggressiveness: int = 2
    vadPadMs: int = 250
    vadMinSegmentSec: float = 1.0

class SummaryRequest(BaseModel):
    recordingId: UUID
    transcript: str

def verify_internal_key(x_internal_key: Optional[str]):
    """
    Spring -> AI 서버 내부 통신용 인증 헤더를 검증한다.
    헤더 이름: X-Internal-Key
    """
    if INTERNAL_API_KEY is None:
        # 서버 설정 문제
        raise HTTPException(
            status_code=500,
            detail="INTERNAL_API_KEY is not configured on AI server.",
        )

    if x_internal_key is None:
        raise HTTPException(status_code=401, detail="X-Internal-Key header is missing.")

    if x_internal_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


@app.post("/internal/transcriptions")
async def create_transcription(
    recordingId: str = Form(...),
    language: str = Form("ko"),
    vadEnabled: bool = Form(False),
    maxSegmentSec: int = Form(25),
    vadAggressiveness: int = Form(2),
    vadPadMs: int = Form(250),
    vadMinSegmentSec: float = Form(1.0),
    file: UploadFile = File(...),
    x_internal_key: Optional[str] = Header(default=None, alias="X-Internal-Key"),
):
    verify_internal_key(x_internal_key)
    return await run_stt(
        recording_id=recordingId,
        language=language,
        upload_file=file,
        vad_enabled=vadEnabled,
        max_segment_sec=maxSegmentSec,
        vad_aggressiveness=vadAggressiveness,
        vad_pad_ms=vadPadMs,
        vad_min_segment_sec=vadMinSegmentSec,
    )

@app.post("/internal/transcriptions/by-url")
async def create_transcription_by_url(
    payload: TranscriptionByUrlRequest,
    x_internal_key: Optional[str] = Header(default=None, alias="X-Internal-Key"),
):
    verify_internal_key(x_internal_key)

    return await run_stt_from_url(
        recording_id=payload.recordingId,
        language=payload.language,
        audio_url=payload.audioUrl,
        vad_enabled=payload.vadEnabled,
        max_segment_sec=payload.maxSegmentSec,
        vad_aggressiveness=payload.vadAggressiveness,
        vad_pad_ms=payload.vadPadMs,
        vad_min_segment_sec=payload.vadMinSegmentSec,
    )

@app.post("/internal/summarizes")
async def create_summary(
    request: SummaryRequest,  # dict 대신 모델 사용
    x_internal_key: Optional[str] = Header(default=None, alias="X-Internal-Key"),
):
    verify_internal_key(x_internal_key)
    # request 객체를 dict로 변환하여 전달
    return await run_summary(request.model_dump())