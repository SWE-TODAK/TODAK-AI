from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import UploadFile
from openai import OpenAI
from config import OPENAI_API_KEY

client = OpenAI()


def _fallback(
    recording_id: str,
    consultation_id: str,
    language: str,
    error: str | None = None,
) -> Dict[str, Any]:
    """
    Whisper 호출 실패 시 fallback 응답.
    """
    now = datetime.now(timezone.utc).isoformat()
    meta = {
        "provider": "openai",
        "model": "none",
        "processedAt": now,
        "fallback": True,
    }
    if error:
        meta["error"] = error

    return {
        "status": 200,
        "message": "STT 변환이 완료되었습니다. (fallback)",
        "data": {
            "recordingId": recording_id,
            "consultationId": consultation_id,
            "duration": 0,
            "language": language,
            "transcript": "",
            "meta": meta,
        },
    }


async def run_stt(
    recording_id: str,
    consultation_id: str,
    language: str,
    upload_file: UploadFile,
) -> Dict[str, Any]:
    """
    STT 변환 메인 함수. (timestamp 제거 버전)
    Spring → AI 서버 : multipart/form-data 업로드
    """

    try:
        # 1️. 파일 전체 bytes 읽기
        data: bytes = await upload_file.read()
        if not data:
            return _fallback(
                recording_id,
                consultation_id,
                language,
                "Empty file uploaded",
            )

        # 2️. Whisper API 호출 (filename + bytes)
        filename = upload_file.filename or "audio.wav"

        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=(filename, data),
            language=language,
        )

        # 3️. transcript 추출
        transcript: str = getattr(result, "text", "") or ""

        processed_at = datetime.now(timezone.utc).isoformat()

        # 4️.  최종 응답
        return {
            "status": 200,
            "message": "STT 변환이 완료되었습니다.",
            "data": {
                "recordingId": recording_id,
                "consultationId": consultation_id,
                "duration": 0,
                "language": language,
                "transcript": transcript.strip(),
                "meta": {
                    "provider": "openai",
                    "model": "whisper-1",
                    "processedAt": processed_at,
                },
            },
        }

    except Exception as e:
        return _fallback(recording_id, consultation_id, language, str(e))
