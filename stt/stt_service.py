from __future__ import annotations

import asyncio
import os
import random
import tempfile
from typing import Any

import httpx
from fastapi import UploadFile
from openai import AsyncOpenAI

from config import OPENAI_API_KEY
from .audio_utils import write_temp_file, ffprobe_meta, to_wav_16k_mono_enhanced
from .vad_utils import split_by_vad

PREPROCESS_VERSION = "stt_preprocess_v3_retry_partial_safe"

MAX_CONCURRENT_TRANSCRIBE = 4
_transcribe_semaphore: asyncio.Semaphore | None = None
_transcribe_semaphore_loop = None


def get_semaphore() -> asyncio.Semaphore:
    global _transcribe_semaphore, _transcribe_semaphore_loop
    loop = asyncio.get_running_loop()
    if _transcribe_semaphore is None or _transcribe_semaphore_loop is not loop:
        _transcribe_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRANSCRIBE)
        _transcribe_semaphore_loop = loop
    return _transcribe_semaphore


def _build_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=OPENAI_API_KEY)


def _classify_error(exc: Exception) -> tuple[str, bool]:
    msg = str(exc).lower()

    retry_keywords = [
        "timeout",
        "temporarily unavailable",
        "rate limit",
        "429",
        "500",
        "502",
        "503",
        "504",
        "connection",
        "api connection",
    ]
    if any(k in msg for k in retry_keywords):
        return ("retryable_error", True)

    if "invalid_api_key" in msg or "authentication" in msg:
        return ("auth_error", False)

    if "unsupported" in msg or "invalid file format" in msg:
        return ("invalid_audio", False)

    return ("unknown_error", False)


def _fallback(
    recording_id: str,
    error: str,
    error_type: str = "unknown_error",
    retry_hint: bool = False,
    extra_meta: dict[str, Any] | None = None,
) -> dict:
    meta = {
        "provider": "openai",
        "model": "whisper-1",
        "fallback": True,
        "retryHint": retry_hint,
        "errorType": error_type,
        "error": error,
        "processedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "preprocessVersion": PREPROCESS_VERSION,
        "jobStatus": "RETRYABLE_FAILED" if retry_hint else "FAILED",
    }
    if extra_meta:
        meta.update(extra_meta)

    return {
        "status": 200,
        "message": "STT 처리 실패",
        "data": {
            "recordingId": recording_id,
            "transcript": "",
            "language": "ko",
            "meta": meta,
        }
    }


async def _sleep_backoff(attempt: int) -> None:
    base = min(2 ** (attempt - 1), 8)
    jitter = random.uniform(0, 0.5)
    await asyncio.sleep(base + jitter)


async def _transcribe_segment_with_retry(
    client: AsyncOpenAI,
    seg_path: str,
    idx: int,
    language: str = "ko",
    max_attempts: int = 3,
) -> dict:
    for attempt in range(1, max_attempts + 1):
        try:
            async with get_semaphore():
                with open(seg_path, "rb") as f:
                    resp = await client.audio.transcriptions.create(
                        model="whisper-1",
                        file=f,
                        language=language,
                    )

            text = getattr(resp, "text", "") or ""
            return {
                "ok": True,
                "index": idx,
                "text": text.strip(),
                "attempts": attempt,
            }

        except Exception as e:
            error_type, retry_hint = _classify_error(e)

            if retry_hint and attempt < max_attempts:
                await _sleep_backoff(attempt)
                continue

            return {
                "ok": False,
                "index": idx,
                "text": "",
                "attempts": attempt,
                "error": str(e),
                "errorType": error_type,
                "retryHint": retry_hint,
            }


async def _download_with_retry(url: str, max_attempts: int = 3) -> bytes:
    last_exc = None

    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.content

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in (429, 500, 502, 503, 504) and attempt < max_attempts:
                last_exc = e
                await _sleep_backoff(attempt)
                continue
            raise

        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
            last_exc = e
            if attempt < max_attempts:
                await _sleep_backoff(attempt)
                continue
            raise

    raise last_exc if last_exc else RuntimeError("download failed")


async def _save_upload_to_temp(upload_file: UploadFile, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    try:
        with open(path, "wb") as f:
            while True:
                chunk = await upload_file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        return path
    except Exception:
        try:
            os.remove(path)
        except Exception:
            pass
        raise


def _guess_suffix_from_content_type(content_type: str | None) -> str:
    ct = (content_type or "").lower()
    if "wav" in ct:
        return ".wav"
    if "mpeg" in ct or "mp3" in ct:
        return ".mp3"
    if "m4a" in ct or "mp4" in ct:
        return ".m4a"
    if "ogg" in ct:
        return ".ogg"
    if "webm" in ct:
        return ".webm"
    return ".bin"


async def run_stt(
    recording_id: str,
    upload_file: UploadFile,
    language: str = "ko",
    **kwargs,
) -> dict:
    suffix = os.path.splitext(upload_file.filename or "")[-1] or ".bin"
    src_path = None

    try:
        src_path = await _save_upload_to_temp(upload_file, suffix=suffix)
        return await run_stt_from_path(
            recording_id=recording_id,
            src_path=src_path,
            language=language,
            **kwargs,
        )
    except Exception as e:
        error_type, retry_hint = _classify_error(e)
        return _fallback(
            recording_id,
            error=str(e),
            error_type=error_type,
            retry_hint=retry_hint,
        )
    finally:
        try:
            await upload_file.close()
        except Exception:
            pass
        if src_path and os.path.exists(src_path):
            os.remove(src_path)


async def run_stt_from_url(
    recording_id: str,
    audio_url: str,
    language: str = "ko",
    **kwargs,
) -> dict:
    src_path = None
    try:
        data = await _download_with_retry(audio_url, max_attempts=3)
        content_type = kwargs.get("content_type")
        suffix = _guess_suffix_from_content_type(content_type)
        src_path = write_temp_file(data, suffix=suffix)

        return await run_stt_from_path(
            recording_id=recording_id,
            src_path=src_path,
            language=language,
            **kwargs,
        )
    except Exception as e:
        error_type, retry_hint = _classify_error(e)
        return _fallback(
            recording_id,
            error=str(e),
            error_type=error_type,
            retry_hint=retry_hint,
        )
    finally:
        if src_path and os.path.exists(src_path):
            os.remove(src_path)


async def run_stt_from_path(
    recording_id: str,
    src_path: str,
    language: str = "ko",
    **kwargs,
) -> dict:
    wav_path = None
    segment_paths: list[str] = []
    client = _build_client()

    try:
        meta_before = await asyncio.to_thread(ffprobe_meta, src_path)
        wav_path = await asyncio.to_thread(to_wav_16k_mono_enhanced, src_path)
        meta_after = await asyncio.to_thread(ffprobe_meta, wav_path)

        duration_sec = meta_after.get("duration_sec", 0.0)
        vad_enabled = kwargs.get("vad_enabled", True)

        if duration_sec <= 60:
            vad_enabled = False

        if vad_enabled:
            vad_kwargs = {
                "max_segment_sec": kwargs.get("max_segment_sec", 25),
                "min_segment_sec": kwargs.get("min_segment_sec", 1.0),
                "vad_aggressiveness": kwargs.get("vad_aggressiveness", 1),
                "frame_ms": kwargs.get("frame_ms", 30),
                "pad_ms": kwargs.get("pad_ms", 800),
            }
            segment_paths = await asyncio.to_thread(split_by_vad, wav_path, **vad_kwargs)
        else:
            segment_paths = [wav_path]

        tasks = [
            _transcribe_segment_with_retry(client, seg_path, idx, language=language)
            for idx, seg_path in enumerate(segment_paths)
        ]
        results = await asyncio.gather(*tasks)

        results = sorted(results, key=lambda x: x["index"])

        failed = [r for r in results if not r["ok"]]
        success = [r for r in results if r["ok"]]

        if failed:
            retry_hint = any(r.get("retryHint", False) for r in failed)
            return _fallback(
                recording_id,
                error="one or more segments failed",
                error_type="partial_failure",
                retry_hint=retry_hint,
                extra_meta={
                    "segmentCount": len(results),
                    "successSegmentCount": len(success),
                    "failedSegmentCount": len(failed),
                    "failedSegmentIndexes": [r["index"] for r in failed],
                    "segmentFailures": [
                        {
                            "index": r["index"],
                            "errorType": r.get("errorType"),
                            "retryHint": r.get("retryHint"),
                            "error": r.get("error"),
                        }
                        for r in failed[:5]
                    ],
                    "preprocess": {
                        "target": "wav_pcm_s16le_16k_mono",
                        "vadEnabled": vad_enabled,
                        "segmentCount": len(segment_paths),
                    },
                },
            )

        transcript = "\n".join(r["text"] for r in success if r["text"]).strip()

        return {
            "status": 200,
            "message": "STT 변환이 완료되었습니다.",
            "data": {
                "recordingId": recording_id,
                "transcript": transcript,
                "language": language,
                "meta": {
                    "provider": "openai",
                    "model": "whisper-1",
                    "processedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                    "preprocessVersion": PREPROCESS_VERSION,
                    "source": meta_before,
                    "preprocess": {
                        "target": "wav_pcm_s16le_16k_mono",
                        "vadEnabled": vad_enabled,
                        "segmentCount": len(segment_paths),
                    },
                    "result": meta_after,
                    "fallback": False,
                    "allSegmentsSucceeded": True,
                }
            }
        }

    except Exception as e:
        error_type, retry_hint = _classify_error(e)
        return _fallback(
            recording_id,
            error=str(e),
            error_type=error_type,
            retry_hint=retry_hint,
        )
    finally:
        for path in set(segment_paths):
            if path != wav_path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

        if wav_path and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass