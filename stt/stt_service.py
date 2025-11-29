# backend/ai/stt/stt_service.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import UploadFile
from openai import OpenAI

from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)


def _build_fallback_result(
    recording_id: str, consultation_id: str, language: str, error: str | None = None
) -> Dict[str, Any]:
    """
    Whisper 호출 실패 시 사용할 fallback 응답.
    에러 메시지도 meta에 같이 넣어준다.
    """
    now = datetime.now(timezone.utc).isoformat()
    meta: Dict[str, Any] = {
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
            "sttResult": [],
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
    /internal/transcriptions 에서 호출되는 메인 함수.

    Parameters (Spring → AI, multipart/form-data):
        - recordingId (str)
        - consultationId (str)
        - language (str, default "ko")
        - file (UploadFile)

    Returns (AI → Spring):
        README_AI_PIPELINE.md 의 "STT Success Response" 구조와 동일한 JSON
    """
    try:
        # 1️⃣ OpenAI Whisper 호출 (가장 단순한 형태)
        audio_binary = upload_file.file

        # whisper-1 기본 응답은 {"text": "..."} 형태
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_binary,
            language=language,
        )

        # 새 SDK에서는 transcription.text 로 접근
        text: str = getattr(transcription, "text", "") or ""

        # duration 은 일단 0으로 두고, 나중에 필요하면 별도로 계산하거나
        # 다른 응답 필드에서 읽도록 확장 가능
        duration = 0

        # MVP: 전체 텍스트를 하나의 segment 로 묶어서 반환
        stt_segments: List[Dict[str, str]] = []
        if text.strip():
            stt_segments.append(
                {
                    "timestamp": "00:00:00",
                    "text": text.strip(),
                }
            )

        processed_at = datetime.now(timezone.utc).isoformat()

        return {
            "status": 200,
            "message": "STT 변환이 완료되었습니다.",
            "data": {
                "recordingId": recording_id,
                "consultationId": consultation_id,
                "duration": duration,
                "language": language,
                "sttResult": stt_segments,
                "meta": {
                    "provider": "openai",
                    "model": "whisper-1",
                    "processedAt": processed_at,
                },
            },
        }

    except Exception as e:
        # 여기서 에러 내용을 fallback 응답에 같이 실어 보내자 (디버깅용)
        return _build_fallback_result(recording_id, consultation_id, language, str(e))
