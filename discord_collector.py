import os
import json
import sqlite3
import re
import discord
from datetime import datetime
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from deep_translator import GoogleTranslator
from langdetect import detect, DetectorFactory
from openai import OpenAI

DetectorFactory.seed = 0

KST = ZoneInfo("Asia/Seoul")

# =====================
# 환경 변수
# =====================

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not DISCORD_TOKEN:
    raise RuntimeError("환경변수 DISCORD_TOKEN이 설정되지 않았습니다.")

if not GOOGLE_CREDENTIALS:
    raise RuntimeError("환경변수 GOOGLE_CREDENTIALS가 설정되지 않았습니다.")

if not OPENAI_API_KEY:
    raise RuntimeError("환경변수 OPENAI_API_KEY가 설정되지 않았습니다.")

# =====================
# 수집 대상 채널
# =====================

TARGET_CHANNEL_IDS = {
    869470698035363840,
    1310800072338051133,
    869471377235800064,
    1366999860645072948,
    1301432300088725555,
}

# =====================
# Google Sheets 설정
# =====================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1F2Smu5Z3JbQ5s693meQhHybMhB5vGRLTWKW81uGKgdY"
SHEET_NAME = "디스코드 동향"
GLOSSARY_SHEET_NAME = "용어집"

# =====================
# SQLite 설정
# =====================

DB_PATH = "discord_messages.db"

# =====================
# 분류 키워드
# =====================

CATEGORY_KEYWORDS = {
    "bug": [
        "bug", "crash", "error", "broken", "glitch", "freeze", "stuck",
        "disconnect", "lag", "failed", "not working",
        "баг", "ошибка", "вылет", "зависает", "лагает", "не работает",
        "сломано", "отключается",
        "错误", "錯誤", "闪退", "閃退", "卡顿", "卡頓", "崩溃", "崩潰",
        "无法进入", "無法進入", "进不去", "進不去", "无法加载", "無法加載",
        "断线", "斷線", "掉线", "掉線"
    ],
    "issue": [
        "problem", "concern", "complaint", "unfair", "pay to win", "p2w",
        "balance", "matchmaking", "too hard", "too easy", "delay",
        "проблема", "жалоба", "нечестно", "баланс", "слишком сложно",
        "слишком легко",
        "问题", "問題", "不公平", "平衡", "匹配", "太难", "太難", "太简单", "太簡單"
    ],
    "positive": [
        "good", "great", "love", "awesome", "nice", "fun", "amazing",
        "excellent", "like it", "well done",
        "хорошо", "отлично", "нравится", "люблю", "супер", "класс",
        "好玩", "很好", "不错", "不錯", "喜欢", "喜歡", "很棒", "优秀", "優秀"
    ],
    "negative": [
        "bad", "terrible", "awful", "hate", "boring", "disappointed",
        "annoying", "frustrating", "worst", "sucks",
        "плохо", "ужасно", "ненавижу", "скучно", "разочарован",
        "раздражает", "худший",
        "很差", "糟糕", "失望", "无聊", "無聊", "讨厌", "討厭", "很烂", "很爛"
    ]
}

CATEGORY_PRIORITY = ["bug", "issue", "negative", "positive"]

# =====================
# Google 인증 / OpenAI
# =====================

creds_dict = json.loads(GOOGLE_CREDENTIALS)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
service = build("sheets", "v4", credentials=creds)

ai_client = OpenAI(api_key=OPENAI_API_KEY)

# =====================
# 시트 기록
# =====================

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
# 용어집
# =====================

def load_glossary():
    """
    용어집 시트:
    A열 원문 / B열 한국어
    """
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{GLOSSARY_SHEET_NAME}!A:B"
        ).execute()

        values = result.get("values", [])
        glossary = {}

        for row in values:
            if len(row) < 2:
                continue
            src = row[0].strip()
            dst = row[1].strip()
            if src and dst:
                glossary[src] = dst

        print(f"✅ 용어집 로드 완료: {len(glossary)}개")
        return glossary

    except Exception as e:
        print("⚠️ 용어집 로드 실패:", repr(e))
        return {}

GLOSSARY = load_glossary()

def apply_glossary_placeholders(text: str, glossary: dict):
    """
    번역 전에 용어를 보호하기 위해 플레이스홀더로 치환.
    """
    if not text:
        return text, {}

    protected_map = {}
    protected_text = text

    # 긴 용어부터 치환
    sorted_terms = sorted(glossary.items(), key=lambda x: len(x[0]), reverse=True)

    for idx, (src_term, ko_term) in enumerate(sorted_terms):
        placeholder = f"__TERM_{idx}__"

        pattern = re.escape(src_term)
        if re.search(pattern, protected_text, flags=re.IGNORECASE):
            protected_text = re.sub(
                pattern,
                placeholder,
                protected_text,
                flags=re.IGNORECASE
            )
            protected_map[placeholder] = ko_term

    return protected_text, protected_map

def restore_glossary_placeholders(text: str, protected_map: dict):
    restored = text
    for placeholder, ko_term in protected_map.items():
        restored = restored.replace(placeholder, ko_term)
    return restored

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

def translate_with_google(text: str, glossary: dict) -> str:
    try:
        if not text or not text.strip():
            return ""

        protected_text, protected_map = apply_glossary_placeholders(text, glossary)
        translated = GoogleTranslator(source="auto", target="ko").translate(protected_text)
        return restore_glossary_placeholders(translated, protected_map)

    except Exception as e:
        print("구글 번역 실패:", repr(e))
        return text

def translate_with_ai(text: str, glossary: dict, category: str, matched_keywords: list[str]) -> str:
    try:
        if not text or not text.strip():
            return ""

        protected_text, protected_map = apply_glossary_placeholders(text, glossary)

        glossary_text = "\n".join(
            [f"- {src} → {dst}" for src, dst in glossary.items()]
        )[:4000]

        keyword_text = ", ".join(matched_keywords)

        prompt = f"""
너는 게임 커뮤니티 모니터링용 번역가다.

목표:
- 아래 메시지를 자연스러운 한국어로 번역한다.
- bug / issue / positive / negative 같은 뉘앙스를 유지한다.
- 과장하지 말고, 원문의 불만/칭찬/이슈 톤을 보존한다.
- 플레이스홀더(__TERM_x__)는 절대 변형하지 않는다.
- 용어집에 해당하는 표현은 한국어 게임 운영 실무 용어로 반영한다.

분류: {category}
매칭 키워드: {keyword_text}

용어집:
{glossary_text}

번역할 원문:
{protected_text}
""".strip()

        response = ai_client.responses.create(
            model="gpt-5.4",
            input=prompt
        )

        translated = response.output_text.strip()
        translated = restore_glossary_placeholders(translated, protected_map)
        return translated

    except Exception as e:
        print("AI 번역 실패:", repr(e))
        return translate_with_google(text, glossary)

def translate_to_korean(text: str, category: str, matched_keywords: list[str], glossary: dict) -> str:
    # 매칭 키워드가 있으면 AI 번역, 없으면 구글 번역
    if matched_keywords:
        return translate_with_ai(text, glossary, category, matched_keywords)
    return translate_with_google(text, glossary)

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
    language_code = detect_language_code(original_text)
    category, matched_keywords = classify_message(original_text)

    translated_text = translate_to_korean(
        text=original_text,
        category=category,
        matched_keywords=matched_keywords,
        glossary=GLOSSARY,
    )

    collected_at_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    created_at_kst = message.created_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")

    row = [
        collected_at_kst,                                 # A 수집시간
        str(message.guild.name) if message.guild else "", # B 서버명
        str(message.channel.name),                        # C 채널명
        str(message.author),                              # D 작성자
        original_text,                                    # E 원문
        translated_text,                                  # F 번역
        language_code,                                    # G 언어코드
        category,                                         # H 분류
        ", ".join(matched_keywords),                      # I 매칭 키워드
        msg_link,                                         # J 메시지 링크
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
            created_at=created_at_kst,
            collected_at=collected_at_kst,
        )

        append_to_sheet(row)
        print("✅ DB + 시트 저장 완료:", row)

    except Exception as e:
        print("❌ 저장 실패:", repr(e))

client.run(DISCORD_TOKEN)
