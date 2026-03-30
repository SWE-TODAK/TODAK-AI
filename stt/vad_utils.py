from __future__ import annotations

import os
import subprocess
import tempfile
from typing import List, Tuple
import webrtcvad

FFMPEG_TIMEOUT_SEC = 60


def _run_cmd(cmd: list[str], timeout: int = FFMPEG_TIMEOUT_SEC) -> None:
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
        )
        if p.returncode != 0:
            raise RuntimeError(f"[cmd failed] {' '.join(cmd)}\n{p.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"[cmd timeout] {' '.join(cmd)}")


def _extract_pcm16le_16k_mono(wav_path: str) -> bytes:
    cmd = [
        "ffmpeg", "-y",
        "-i", wav_path,
        "-ac", "1",
        "-ar", "16000",
        "-f", "s16le",
        "pipe:1",
    ]
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT_SEC,
        )
        if p.returncode != 0:
            raise RuntimeError(f"ffmpeg pcm extract failed:\n{p.stderr.decode(errors='ignore')}")
        return p.stdout
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"[cmd timeout] {' '.join(cmd)}")


def _cut_wav(input_wav: str, start_sec: float, end_sec: float) -> str:
    fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_sec:.3f}",
        "-to", f"{end_sec:.3f}",
        "-i", input_wav,
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        out_path
    ]
    _run_cmd(cmd, timeout=30)
    return out_path


def _merge_close_segments(
    segs_sec: List[Tuple[float, float]],
    merge_gap_sec: float = 0.4,
) -> List[Tuple[float, float]]:
    if not segs_sec:
        return []

    merged = [segs_sec[0]]
    for s, e in segs_sec[1:]:
        prev_s, prev_e = merged[-1]
        if s - prev_e <= merge_gap_sec:
            merged[-1] = (prev_s, max(prev_e, e))
        else:
            merged.append((s, e))
    return merged


def split_by_vad(
    wav_path: str,
    max_segment_sec: int = 25,
    min_segment_sec: float = 1.0,
    vad_aggressiveness: int = 1,
    frame_ms: int = 30,
    pad_ms: int = 800,
) -> List[str]:
    if frame_ms not in (10, 20, 30):
        raise ValueError("frame_ms must be 10, 20, or 30")

    pcm = _extract_pcm16le_16k_mono(wav_path)
    sample_rate = 16000
    bytes_per_sample = 2
    frame_bytes = int(sample_rate * (frame_ms / 1000.0) * bytes_per_sample)

    vad = webrtcvad.Vad(vad_aggressiveness)

    speech_flags = []
    for i in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        frame = pcm[i:i + frame_bytes]
        speech_flags.append(vad.is_speech(frame, sample_rate))

    if not any(speech_flags):
        return [wav_path]

    segments_frames: List[Tuple[int, int]] = []
    in_speech = False
    start = 0

    for idx, flag in enumerate(speech_flags):
        if flag and not in_speech:
            in_speech = True
            start = idx
        elif not flag and in_speech:
            in_speech = False
            segments_frames.append((start, idx))

    if in_speech:
        segments_frames.append((start, len(speech_flags)))

    pad_frames = int(pad_ms / frame_ms)
    segs_sec: List[Tuple[float, float]] = []
    for s_f, e_f in segments_frames:
        s_f = max(0, s_f - pad_frames)
        e_f = min(len(speech_flags), e_f + pad_frames)
        s = s_f * (frame_ms / 1000.0)
        e = e_f * (frame_ms / 1000.0)
        if (e - s) >= min_segment_sec:
            segs_sec.append((s, e))

    if not segs_sec:
        return [wav_path]

    segs_sec = _merge_close_segments(segs_sec, merge_gap_sec=0.4)

    final_segs: List[Tuple[float, float]] = []
    for s, e in segs_sec:
        cur = s
        while cur < e:
            nxt = min(cur + max_segment_sec, e)
            if (nxt - cur) >= min_segment_sec:
                final_segs.append((cur, nxt))
            cur = nxt

    out_paths: List[str] = []
    for s, e in final_segs:
        out_paths.append(_cut_wav(wav_path, s, e))

    return out_paths if out_paths else [wav_path]