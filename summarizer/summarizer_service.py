"""
Summarizer Service

- Spring → AI 서버 내부 API: POST /internal/summarizes 에서 호출하는 실제 구현부
- 기능:
    1) prompt.md 를 system 프롬프트로 사용
    2) OpenAI Chat API 를 호출해 요약 JSON 생성
    3) README_AI_PIPELINE.md 에 정의된 응답 포맷으로 래핑하여 반환
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from openai import OpenAI
from config import OPENAI_API_KEY

# OpenAI 클라이언트 (환경변수에서 키 로드)
client = OpenAI(api_key=OPENAI_API_KEY)

# prompt.md 파일 경로
PROMPT_PATH = Path(__file__).with_name("prompt.md")


def _load_system_prompt() -> str:
    """
    수정된 영문 prompt.md를 로드합니다.
    """
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")

    # 파일이 없을 경우를 대비한 최소한의 영문 지시사항 (Fallback)
    return (
        "You are a professional medical assistant. Summarize the transcript into JSON.\n"
        "Fields: 'intro' (one-line summary), 'content' (detailed markdown summary).\n"
        "Use only the provided information. Do not hallucinate."
    )


def _build_fallback_content(transcript: str) -> Dict[str, Any]:
    """
    OpenAI 호출 실패 시 Spring DTO 구조에 맞춘 기본값 반환
    """
    trimmed = transcript[:50] + ("..." if len(transcript) > 50 else "")
    return {
        "intro": "[요약 생성 실패] 녹음 파일 확인 필요",
        "content": f"AI 요약 생성 중 오류가 발생했습니다. 원본 텍스트 일부: {trimmed}"
    }


async def run_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Spring의 SummaryRequest를 받아 AiSummaryResponse 구조로 반환합니다.
    """
    # 1. 입력값 추출
    recording_id = payload.get("recordingId")
    transcript = payload.get("transcript", "")

    system_prompt = _load_system_prompt()

    try:
        # 2. OpenAI 호출 (temperature를 낮춰 환각 방지)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            temperature=0,  # 0에 가까울수록 임의 판단을 하지 않고 사실에만 근거함
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": f"Summarize this transcript strictly based on facts:\n\n{transcript}",
                },
            ],
        )

        raw_content = completion.choices[0].message.content or "{}"
        parsed = json.loads(raw_content)

        # 3. 필드 매핑 (Spring AiSummaryData 필드명인 intro, content 추출)
        intro = parsed.get("intro", "요약 내용 없음")
        content = parsed.get("content", "상세 내용 없음")

    except Exception as e:
        # 에러 발생 시 로그를 남기고 fallback 반환
        print(f"Error during AI summary: {e}")
        fallback = _build_fallback_content(transcript)
        intro = fallback["intro"]
        content = fallback["content"]

    # 4. Spring의 AiSummaryResponse 구조와 1:1 매칭
    # { "data": { "intro": "...", "content": "..." } }
    return {
        "data": {
            "intro": intro,
            "content": content
        }
    }