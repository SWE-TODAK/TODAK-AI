import subprocess
import tempfile
import os
import json

FFMPEG_TIMEOUT_SEC = 120


def _run(cmd: list[str]) -> str:
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=FFMPEG_TIMEOUT_SEC,
        )
        if p.returncode != 0:
            raise RuntimeError(f"[cmd failed] {' '.join(cmd)}\n{p.stderr}")
        return p.stdout
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"[cmd timeout] {' '.join(cmd)}")


def write_temp_file(data: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(path, "wb") as f:
        f.write(data)
    return path


def ffprobe_meta(path: str) -> dict:
    out = _run([
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path
    ])
    data = json.loads(out)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    astream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    return {
        "codec": astream.get("codec_name"),
        "sample_rate": int(astream.get("sample_rate")) if astream.get("sample_rate") else None,
        "channels": astream.get("channels"),
        "bit_rate": int(astream.get("bit_rate")) if astream.get("bit_rate")
        else (int(fmt.get("bit_rate")) if fmt.get("bit_rate") else None),
        "duration_sec": float(fmt.get("duration")) if fmt.get("duration") else 0.0,
    }


def to_wav_16k_mono_enhanced(src_path: str) -> str:
    fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)

    afilter = "highpass=f=80,lowpass=f=8000,dynaudnorm=f=75:g=15"

    _run([
        "ffmpeg", "-y",
        "-i", src_path,
        "-ac", "1",
        "-ar", "16000",
        "-af", afilter,
        "-c:a", "pcm_s16le",
        out_path
    ])
    return out_path