import os
import json
import sqlite3
import discord
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from deep_translator import GoogleTranslator
from langdetect import detect, DetectorFactory

DetectorFactory.seed = 0

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
    1301432300088725555,
}

# Google Sheets
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1F2Smu5Z3JbQ5s693meQhHybMhB5vGRLTWKW81uGKgdY"
SHEET_NAME = "디스코드 동향"

# SQLite
DB_PATH = "discord_messages.db"

# =====================
# 분류 키워드
# =====================

CATEGORY_KEYWORDS = {
    "bug": [
        # English
        "bug", "crash", "error", "broken", "glitch", "freeze", "stuck",
        "disconnect", "lag", "failed", "not working",

        # Russian
        "баг", "ошибка", "вылет", "зависает", "лагает", "не работает",
        "сломано", "отключается",

        # Chinese Simplified / Traditional
        "错误", "錯誤", "闪退", "閃退", "卡顿", "卡頓", "崩溃", "崩潰",
        "无法进入", "無法進入", "进不去", "進不去", "无法加载", "無法加載",
        "断线", "斷線", "掉线", "掉線"
    ],
    "issue": [
        # English
        "problem", "concern", "complaint", "unfair", "pay to win", "p2w",
        "balance", "matchmaking", "too hard", "too easy", "delay",

        # Russian
        "проблема", "жалоба", "нечестно", "баланс", "слишком сложно",
        "слишком легко",

        # Chinese Simplified / Traditional
        "问题", "問題", "不公平", "平衡", "匹配", "太难", "太難", "太简单", "太簡單"
    ],
    "positive": [
        # English
        "good", "great", "love", "awesome", "nice", "fun", "amazing",
        "excellent", "like it", "well done",

        # Russian
        "хорошо", "отлично", "нравится", "люблю", "супер", "класс",

        # Chinese Simplified / Traditional
        "好玩", "很好", "不错", "不錯", "喜欢", "喜歡", "很棒", "优秀", "優秀"
    ],
    "negative": [
        # English
        "bad", "terrible", "awful", "hate", "boring", "disappointed",
        "annoying", "frustrating", "worst", "sucks",

        # Russian
        "плохо", "ужасно", "ненавижу", "скучно", "разочарован",
        "раздражает", "худший",

        # Chinese Simplified / Traditional
        "很差", "糟糕", "失望", "无聊", "無聊", "讨厌", "討厭", "很烂", "很爛"
    ]
}

CATEGORY_PRIORITY = ["bug", "issue", "negative", "positive"]

# =====================
# Google 인증
# =====================

creds_dict = json.loads(GOOGLE_CREDENTIALS)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
service = build("sheets", "v4", credentials=creds)

def append_to_sheet(data):
    sheet = service.spreadsheets()
    body = {"values": [data]}
    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A:J",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()

# =====================
# SQLite
# =====================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS discord_messages (
            message_id TEXT PRIMARY KEY,
            message_link TEXT,
            guild_name TEXT,
            channel_id TEXT,
            channel_name TEXT,
            author_name TEXT,
            author_id TEXT,
            content TEXT,
            translated_text TEXT,
            language_code TEXT,
            category TEXT,
            matched_keywords TEXT,
            created_at TEXT,
            collected_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def message_exists(message_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM discord_messages WHERE message_id = ?", (message_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None

def save_message(
    message_id: str,
    message_link: str,
    guild_name: str,
    channel_id: str,
    channel_name: str,
    author_name: str,
    author_id: str,
    content: str,
    translated_text: str,
    language_code: str,
    category: str,
    matched_keywords: str,
    created_at: str,
    collected_at: str,
):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO discord_messages (
            message_id, message_link, guild_name, channel_id, channel_name,
            author_name, author_id, content, translated_text, language_code,
            category, matched_keywords, created_at, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        message_id, message_link, guild_name, channel_id, channel_name,
        author_name, author_id, content, translated_text, language_code,
        category, matched_keywords, created_at, collected_at
    ))
    conn.commit()
    conn.close()

# =====================
# 번역 / 언어감지 / 분류
# =====================

def translate_to_korean(text: str) -> str:
    try:
        if not text or not text.strip():
            return ""
        return GoogleTranslator(source="auto", target="ko").translate(text)
    except Exception as e:
        print("번역 실패:", repr(e))
        return text

def detect_language_code(text: str) -> str:
    try:
        if not text or not text.strip():
            return "unknown"
        return detect(text)
    except Exception:
        return "unknown"

def classify_message(content: str):
    text = (content or "").lower()
    matched = {cat: [] for cat in CATEGORY_KEYWORDS}

    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text:
                matched[category].append(kw)

    for category in CATEGORY_PRIORITY:
        if matched[category]:
            return category, matched[category]

    return "neutral", []

# =====================
# Discord 설정
# =====================

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    init_db()
    print(f"로그인 완료: {client.user}")
    print("수집 대상 채널 ID:", TARGET_CHANNEL_IDS)

@client.event
async def on_message(message):
    if message.author.bot:
        return

    if message.channel.id not in TARGET_CHANNEL_IDS:
        return

    msg_id = str(message.id)
    msg_link = message.jump_url

    if message_exists(msg_id):
        print(f"↳ 중복 메시지 스킵: {msg_id}")
        return

    original_text = message.content or ""
    translated_text = translate_to_korean(original_text)
    language_code = detect_language_code(original_text)
    category, matched_keywords = classify_message(original_text)

    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),      # A 수집시간
        str(message.guild.name) if message.guild else "",  # B 서버명
        str(message.channel.name),                         # C 채널명
        str(message.author),                               # D 작성자
        original_text,                                     # E 원문
        translated_text,                                   # F 번역
        language_code,                                     # G 언어코드
        category,                                          # H 분류
        ", ".join(matched_keywords),                       # I 매칭 키워드
        msg_link,                                          # J 메시지 링크
    ]

    try:
        save_message(
            message_id=msg_id,
            message_link=msg_link,
            guild_name=str(message.guild.name) if message.guild else "",
            channel_id=str(message.channel.id),
            channel_name=str(message.channel.name),
            author_name=str(message.author),
            author_id=str(message.author.id),
            content=original_text,
            translated_text=translated_text,
            language_code=language_code,
            category=category,
            matched_keywords=", ".join(matched_keywords),
            created_at=str(message.created_at),
            collected_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        append_to_sheet(row)
        print("✅ DB + 시트 저장 완료:", row)

    except Exception as e:
        print("❌ 저장 실패:", repr(e))

client.run(DISCORD_TOKEN)
