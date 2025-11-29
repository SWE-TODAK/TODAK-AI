from openai import OpenAI
from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

audio_path = r"C:\Users\vsops\Downloads\test_clean.wav"  # 여기에 실제 경로

with open(audio_path, "rb") as f:
    result = client.audio.transcriptions.create(
        model="whisper-1",
        file=f,
        language="ko",
    )

print("=== TRANSCRIPT TEXT ===")
print(result.text if hasattr(result, "text") else result)
