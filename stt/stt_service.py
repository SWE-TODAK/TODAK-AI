# backend/ai/stt/stt_service.py

"""
STT Service

- Spring → AI 서버 내부 API: POST /internal/transcriptions 에서 호출하는 구현부
- 기능:
    1) fileUrl 로부터 음성 파일 다운로드
    2) OpenAI Whisper(또는 gpt-4o-transcribe)로 STT 수행
    3) README_AI_PIPELINE.md 에 정의된 형식으로 STT 결과(segment 리스트) 반환
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests
from openai import OpenAI

from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)


def _seconds_to_timestamp(sec: float) -> str:
    """
    초 단위를 HH:MM:SS 포맷의 문자열로 변환.
    """
    sec_int = int(sec)
    h = sec_int // 3600
    m = (sec_int % 3600) // 60
    s = sec_int % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _download_file(url: str) -> Path:
    """
    fileUrl 로부터 음성 파일을 임시 디렉토리에 다운로드한다.
    """
    resp = requests.get(url, stream=True, timeout=30)
    resp.raise_for_status()

    suffix = Path(url).suffix or ".m4a"
    fd, temp_path = tempfile.mkstemp(suffix=suffix)
    path = Path(temp_path)

    with path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    return path


def _build_fallback_result(recording_id: str, consultation_id: str, language: str) -> Dict[str, Any]:
    """
    Whisper 호출 실패시 사용할 fallback 응답.
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "status": 200,
        "message": "STT 변환이 완료되었습니다. (fallback)",
        "data": {
            "recordingId": recording_id,
            "consultationId": consultation_id,
            "duration": 0,
            "language": language,
            "sttResult": [],
            "meta": {
                "provider": "openai",
                "model": "none",
                "processedAt": now,
                "fallback": True,
            },
        },
    }


async def run_stt(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    /internal/transcriptions 에서 호출되는 메인 함수.

    Parameters (Spring → AI):
        {
          "recordingId": "r-uuid-def",
          "consultationId": "c-uuid-123",
          "fileUrl": "https://.../file.m4a",
          "language": "ko"
        }

    Returns (AI → Spring):
        README_AI_PIPELINE.md 의 "STT Success Response" 구조와 동일한 JSON
    """
    recording_id = payload["recordingId"]
    consultation_id = payload["consultationId"]
    file_url = payload["fileUrl"]
    language = payload.get("language", "ko")

    try:
        # 1️⃣ 파일 다운로드
        audio_path = _download_file(file_url)

        # 2️⃣ OpenAI Whisper 호출 (verbose_json 으로 segment 정보 포함)
        with audio_path.open("rb") as audio_file:
            # 필요에 따라 model 이름 변경 가능
            # - "whisper-1"
            # - "gpt-4o-transcribe"
            # - "gpt-4o-mini-transcribe"
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json",
                language=language,
            )

        # transcription: duration, text, segments 등 포함
        duration = getattr(transcription, "duration", 0) or 0
        segments_raw: List[Any] = getattr(transcription, "segments", []) or []

        stt_segments: List[Dict[str, str]] = []
        for seg in segments_raw:
            # seg.start / seg.end / seg.text 등을 사용 (초 단위)
            start_sec = getattr(seg, "start", 0.0)
            ts = _seconds_to_timestamp(start_sec)
            text = getattr(seg, "text", "").strip()
            if not text:
                continue
            stt_segments.append(
                {
                    "timestamp": ts,
                    "text": text,
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

    except Exception:
        # 에러가 나더라도 Spring 이 전체 플로우를 돌릴 수 있도록
        return _build_fallback_result(recording_id, consultation_id, language)
