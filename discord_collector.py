import os
import json
import discord
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googletrans import Translator

# =====================
# 환경 변수
# =====================

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

# 여러 채널
TARGET_CHANNEL_IDS = {
    869470698035363840,
    1310800072338051133,
    869471377235800064,
    1366999860645072948,
}

# Google Sheets
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1F2Smu5Z3JbQ5s693meQhHybMhB5vGRLTWKW81uGKgdY"
SHEET_NAME = "디스코드 동향"

# =====================
# Google 인증
# =====================

creds_dict = json.loads(GOOGLE_CREDENTIALS)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
service = build("sheets", "v4", credentials=creds)

def append_to_sheet(data):
    sheet = service.spreadsheets()

    body = {
        "values": [data]
    }

    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A:I",  # 👉 I열까지
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()

# =====================
# 번역 기능
# =====================

translator = Translator()

def translate_to_korean(text: str) -> str:
    try:
        result = translator.translate(text, dest='ko')
        return result.text
    except Exception as e:
        print("번역 실패:", e)
        return text  # 👉 실패하면 원문 그대로

# =====================
# 키워드 분류 (간단 버전)
# =====================

def classify_message(text: str):
    text_lower = text.lower()

    keywords = {
        "bug": ["bug", "error", "crash", "broken"],
        "issue": ["problem", "issue", "not working"],
        "positive": ["good", "great", "nice", "love"],
        "negative": ["bad", "terrible", "worst"],
    }

    matched = []

    for category, words in keywords.items():
        for w in words:
            if w in text_lower:
                matched.append(w)
                return category, matched

    return "neutral", matched

# =====================
# Discord 설정
# =====================

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"로그인 완료: {client.user}")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    if message.channel.id not in TARGET_CHANNEL_IDS:
        return

    try:
        # 분류
        category, matched_keywords = classify_message(message.content)

        # 번역 (항상 실행)
        translated_text = translate_to_korean(message.content)

        # 메시지 ID (중복 제거용)
        msg_id = str(message.id)

        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # A
            str(message.guild.name),                      # B
            str(message.channel.name),                    # C
            str(message.author),                          # D
            message.content,                              # E (원문)
            category,                                     # F
            ", ".join(matched_keywords),                  # G
            msg_id,                                       # H
            translated_text                               # I (번역)
        ]

        append_to_sheet(row)

        print("✅ 저장 완료:", row)

    except Exception as e:
        print("❌ 저장 실패:", e)

client.run(DISCORD_TOKEN)
