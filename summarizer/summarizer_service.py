# backend/ai/summarizer/summarizer_service.py

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
from typing import Any, Dict

from openai import OpenAI

from config import OPENAI_API_KEY

# OpenAI 클라이언트 (환경변수에서 키 로드)
client = OpenAI(api_key=OPENAI_API_KEY)

# prompt.md 파일 경로
PROMPT_PATH = Path(__file__).with_name("prompt.md")


def _load_system_prompt() -> str:
    """
    summarizer/prompt.md 내용을 system 프롬프트로 로드한다.
    파일이 없으면 기본 프롬프트를 사용한다.
    """
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")

    # fallback 기본 프롬프트 (prompt.md 가 없을 때만 사용)
    return (
        "당신은 한국어로 의학 진료 요약을 작성하는 의료 비서입니다.\n"
        "입력으로 진료 대화(STT 텍스트)가 주어지면, 다음 JSON 스키마를 따르는 요약을 생성하세요.\n"
        "반드시 JSON 객체만 출력하고, 불필요한 설명 문장은 포함하지 마세요.\n"
        "최상위 키는 content, tags 두 개입니다.\n"
        "content에는 patient_summary, diagnosis, prescriptions, instructions, meta 를 포함합니다.\n"
        "tags에는 keywords(문자열 배열), risk_level(LOW/MEDIUM/HIGH)를 포함합니다."
    )


def _build_fallback_content(transcript: str) -> Dict[str, Any]:
    """
    OpenAI 호출에 실패했을 때 사용할 fallback 내용.
    (최소한의 구조만 맞춘 더미 데이터)
    """
    trimmed = transcript[:100] + ("..." if len(transcript) > 100 else "")

    return {
        "patient_summary": f"[FALLBACK] STT 일부 내용 기반 요약입니다: {trimmed}",
        "diagnosis": ["요약 생성 실패 (fallback)"],
        "prescriptions": [],
        "instructions": [
            "요약 생성에 오류가 발생했습니다. 나중에 다시 시도해 주세요.",
        ],
        "meta": {
            "language": "ko",
            "version": "fallback",
            "model": "none",
        },
    }


async def run_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    /internal/summarizes 에서 호출되는 메인 함수.

    Parameters (Spring → AI):
        {
          "consultationId": "c-uuid-123",
          "recordingId": "r-uuid-def",
          "transcript": "환자: ...\n의사: ..."
        }

    Returns (AI → Spring):
        README_AI_PIPELINE.md 의 "Summarizer Success Response" 구조와 동일한 JSON
    """
    consultation_id = payload["consultationId"]
    recording_id = payload["recordingId"]
    transcript = payload["transcript"]

    system_prompt = _load_system_prompt()

    content: Dict[str, Any]
    tags: Dict[str, Any]

    try:
        # OpenAI Chat Completion 호출
        # 모델 이름은 필요에 따라 변경 가능 (예: gpt-4.1, gpt-4.1-mini 등)
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": (
                        "아래는 한 번의 진료에 대한 전체 대화(STT 텍스트)입니다.\n"
                        "지정된 스키마(summary_schema.json)에 맞는 JSON 형태의 요약을 생성해주세요.\n"
                        "반드시 JSON 객체만 출력하고, 다른 설명 문장은 포함하지 마세요.\n\n"
                        f"{transcript}"
                    ),
                },
            ],
        )

        raw_content = completion.choices[0].message.content or "{}"

        # 모델이 반환한 JSON 문자열 파싱
        parsed = json.loads(raw_content)

        # 모델 프롬프트에 따라 두 가지 케이스를 모두 허용:
        # 1) 최상위에 content/tags 가 있는 경우
        # 2) 최상위가 바로 content 구조인 경우
        if "content" in parsed or "tags" in parsed:
            content = parsed.get("content", {})
            tags = parsed.get("tags", {})
        else:
            content = parsed
            tags = {}

        # meta.language / version / model 이 없으면 기본값 채우기
        meta_in_content = content.get("meta", {})
        meta_in_content.setdefault("language", "ko")
        meta_in_content.setdefault("version", "v1")
        meta_in_content.setdefault("model", "gpt-4.1-mini")
        content["meta"] = meta_in_content

        # tags 기본값 보정
        tags.setdefault("keywords", [])
        tags.setdefault("risk_level", "LOW")

    except Exception as e:
        # OpenAI 호출 실패 시 fallback 구조 사용
        content = _build_fallback_content(transcript)
        tags = {
            "keywords": ["fallback", "error"],
            "risk_level": "LOW",
            "error": str(e),
        }

    processed_at = datetime.now(timezone.utc).isoformat()

    # README_AI_PIPELINE.md 에 정의된 응답 래핑
    return {
        "status": 200,
        "message": "요약 생성이 완료되었습니다.",
        "data": {
            "consultationId": consultation_id,
            "recordingId": recording_id,
            "content": content,
            "tags": tags,
            "meta": {
                "provider": "openai",
                "processedAt": processed_at,
            },
        },
    }
