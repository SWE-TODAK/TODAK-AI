import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY")  # Spring과 AI 서버 사이 인증
