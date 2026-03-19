import os
import json
import discord
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

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
        range=f"{SHEET_NAME}!A:E",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()

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
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            str(message.guild.name),
            str(message.channel.name),
            str(message.author),
            message.content,
        ]

        append_to_sheet(row)

        print("✅ 시트 저장 완료:", row)

    except Exception as e:
        print("❌ 저장 실패:", e)

client.run(DISCORD_TOKEN)
