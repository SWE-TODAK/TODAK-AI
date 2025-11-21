# backend/ai/main.py

from typing import Optional

from fastapi import FastAPI, Header, HTTPException

from config import INTERNAL_API_KEY
from stt.stt_service import run_stt
from summarizer.summarizer_service import run_summary

# FastAPI 인스턴스 (⭐ uvicorn main:app 에서 찾는 변수)
app = FastAPI(
    title="Todak AI Server",
    description="Internal STT + Summarizer API",
    version="1.0.0",
)


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
    payload: dict,
    x_internal_key: Optional[str] = Header(default=None, alias="X-Internal-Key"),
):
    """
    STT 생성 API
    Spring 백엔드에서만 호출하는 내부용 엔드포인트.
    """
    verify_internal_key(x_internal_key)
    # stt/stt_service.py 에 있는 run_stt 호출
    return await run_stt(payload)


@app.post("/internal/summarizes")
async def create_summary(
    payload: dict,
    x_internal_key: Optional[str] = Header(default=None, alias="X-Internal-Key"),
):
    """
    요약 생성 API
    Spring 백엔드에서만 호출하는 내부용 엔드포인트.
    """
    verify_internal_key(x_internal_key)
    # summarizer/summarizer_service.py 에 있는 run_summary 호출
    return await run_summary(payload)
