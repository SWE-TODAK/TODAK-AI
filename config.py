import os
from dotenv import load_dotenv

load_dotenv()  # ✅ 로컬 .env 로드 (Render에서는 무시됨)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY")
