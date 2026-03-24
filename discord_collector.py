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
        # English
        "bug", "crash", "error", "broken", "glitch", "freeze", "stuck",
        "disconnect", "lag", "failed", "not working", "server",

        # Russian
        "баг", "ошибка", "вылет", "зависает", "лагает", "не работает",
        "сломано", "отключается", "сервер",

        # Chinese Simplified / Traditional
        "错误", "錯誤", "闪退", "閃退", "卡顿", "卡頓", "崩溃", "崩潰",
        "无法进入", "無法進入", "进不去", "進不去", "无法加载", "無法加載",
        "断线", "斷線", "掉线", "掉線"
    ],
    "issue": [
        # English
        "problem", "concern", "complaint", "unfair", "pay to win", "p2w",
        "balance", "matchmaking", "too hard", "too easy", "delay", "server",

        # Russian
        "проблема", "жалоба", "нечестно", "баланс", "слишком сложно",
        "слишком легко", "сервер",

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
        "annoying", "frustrating", "worst", "sucks", "fuck",

        # Russian
        "плохо", "ужасно", "ненавижу", "скучно", "разочарован",
        "раздражает", "худший",

        # Chinese Simplified / Traditional
        "很差", "糟糕", "失望", "无聊", "無聊", "讨厌", "討厭", "很烂", "很爛"
    ]
}

CATEGORY_PRIORITY = ["bug", "issue", "negative", "positive"]

# =====================
# 외부 서비스 초기화
# =====================

creds_dict = json.loads(GOOGLE_CREDENTIALS)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
service = build("sheets", "v4", credentials=creds)

ai_client = OpenAI(api_key=OPENAI_API_KEY)


# =====================
# Google Sheets
# =====================

def append_to_sheet(data):
    sheet = service.spreadsheets()
    body = {"values": [data]}
    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A:M",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()


# =====================
# 용어집
# =====================

def load_glossary():
    """
    용어집 구조:
    A: 한국어
    B: 영어
    C: 중국어(간체)
    D: 중국어(번체)
    E: 러시아어
    """
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{GLOSSARY_SHEET_NAME}!A:E"
        ).execute()

        values = result.get("values", [])
        glossary = {}

        # 헤더가 있어도 자동으로 무시되도록 처리
        for row in values:
            if len(row) < 2:
                continue

            ko = row[0].strip()
            if not ko:
                continue

            # B~E 언어를 모두 한국어로 매핑
            for col in row[1:]:
                if not col:
                    continue

                src = col.strip()
                if src:
                    glossary[src] = ko

        print(f"✅ 용어집 로드 완료: {len(glossary)}개")
        return glossary

    except Exception as e:
        print("⚠️ 용어집 로드 실패:", repr(e))
        return {}


def apply_glossary_placeholders(text: str, glossary: dict):
    """
    번역 전에 용어를 플레이스홀더로 보호
    """
    if not text:
        return text, {}

    protected_map = {}
    protected_text = text

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


GLOSSARY = load_glossary()


# =====================
# SQLite
# =====================

def ensure_column_exists(conn, table_name, column_name, column_type):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cur.fetchall()]

    if column_name not in columns:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        conn.commit()


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

    # 기존 테이블에 없을 수 있는 새 컬럼 보강
    ensure_column_exists(conn, "discord_messages", "translation_engine", "TEXT")
    ensure_column_exists(conn, "discord_messages", "translation_quality", "TEXT")
    ensure_column_exists(conn, "discord_messages", "translation_issue_reason", "TEXT")

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
    translation_engine: str,
    translation_quality: str,
    translation_issue_reason: str,
    created_at: str,
    collected_at: str,
):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO discord_messages (
            message_id, message_link, guild_name, channel_id, channel_name,
            author_name, author_id, content, translated_text, language_code,
            category, matched_keywords, translation_engine, translation_quality,
            translation_issue_reason, created_at, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        message_id, message_link, guild_name, channel_id, channel_name,
        author_name, author_id, content, translated_text, language_code,
        category, matched_keywords, translation_engine, translation_quality,
        translation_issue_reason, created_at, collected_at
    ))
    conn.commit()
    conn.close()


# =====================
# 분류 / 언어감지
# =====================

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
# 번역 품질 점검
# =====================

def is_suspicious_translation(original_text: str, translated_text: str):
    reasons = []

    orig = (original_text or "").strip()
    trans = (translated_text or "").strip()

    if not orig:
        return False, []

    if not trans:
        reasons.append("empty_translation")

    if "__TERM_" in trans:
        reasons.append("placeholder_left")

    if len(orig) >= 20 and len(trans) <= 4:
        reasons.append("too_short")

    if len(orig) >= 10:
        ratio = len(trans) / max(len(orig), 1)
        if ratio < 0.2:
            reasons.append("length_too_short_vs_original")
        elif ratio > 3.5:
            reasons.append("length_too_long_vs_original")

    english_words_left = re.findall(r"[A-Za-z]{4,}", trans)
    if len(english_words_left) >= 4:
        reasons.append("too_many_english_words_left")

    if re.search(r"(.)\1{4,}", trans):
        reasons.append("repeated_chars")

    weird_patterns = [
        "몇 가지 캐릭터",
        "기본 캐릭터이",
        "작업광석",
        "반지 위로 만듭니다",
        "어수선한",
    ]
    for p in weird_patterns:
        if p in trans:
            reasons.append(f"weird_pattern:{p}")

    return (len(reasons) > 0), reasons


# =====================
# 번역
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


def translate_with_ai(text: str, glossary: dict, category: str, matched_keywords: list[str], retry: bool = False) -> str:
    try:
        if not text or not text.strip():
            return ""

        protected_text, protected_map = apply_glossary_placeholders(text, glossary)

        glossary_lines = [f"- {src} → {dst}" for src, dst in list(glossary.items())[:200]]
        glossary_text = "\n".join(glossary_lines)

        keyword_text = ", ".join(matched_keywords) if matched_keywords else "없음"

        extra_rule = ""
        if retry:
            extra_rule = """
추가 규칙:
- 이전 번역이 어색했다고 판단되어 재번역 중이다.
- 직역을 피하고 한국 게임 커뮤니티에서 자연스럽게 쓰는 표현으로 번역하라.
- "몇 가지 캐릭터", "작업광석" 같은 어색한 직역을 절대 만들지 마라.
- main character는 문맥상 본캐, alt character는 부캐로 번역한다.
- store는 아이템 보관 문맥이면 '보관하다'로 번역한다.
- cluttered / full / overloaded는 '가득 차 있다', '쌓여 있다'로 번역한다.
"""

        prompt = f"""
너는 게임 커뮤니티 번역가다.

규칙:
1. 직역보다 의미 번역을 우선한다.
2. 게임 문맥에서는 한국 게임 커뮤니티 용어를 사용한다.
3. 감정(불만, 칭찬, 문제 제기)은 유지한다.
4. 플레이스홀더(__TERM_x__)는 절대 바꾸지 않는다.
5. 숫자, 슬롯, 수치 정보는 변형하지 않는다.
6. 결과는 자연스럽고 짧고 명확한 한국어여야 한다.

분류: {category}
매칭 키워드: {keyword_text}

용어집:
{glossary_text}

{extra_rule}

원문:
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
        return text


def smart_translate_to_korean(text: str, category: str, matched_keywords: list[str], glossary: dict):
    """
    반환:
    translated_text, engine_name, quality_flag, reasons
    """
    if matched_keywords:
        first_engine = "AI"
        translated = translate_with_ai(text, glossary, category, matched_keywords, retry=False)
    else:
        first_engine = "Google"
        translated = translate_with_google(text, glossary)

    suspicious, reasons = is_suspicious_translation(text, translated)

    if not suspicious:
        return translated, first_engine, "ok", ""

    print(f"⚠️ 이상 번역 감지 ({first_engine}): {reasons}")

    retry_ai = translate_with_ai(text, glossary, category, matched_keywords, retry=True)
    suspicious_ai, _ = is_suspicious_translation(text, retry_ai)

    if not suspicious_ai:
        return retry_ai, f"{first_engine}->AI_retry", "retried_ok", ", ".join(reasons)

    retry_google = translate_with_google(text, glossary)
    suspicious_google, _ = is_suspicious_translation(text, retry_google)

    if not suspicious_google:
        return retry_google, f"{first_engine}->Google_retry", "retried_ok", ", ".join(reasons)

    protected_text, protected_map = apply_glossary_placeholders(text, glossary)
    fallback_text = restore_glossary_placeholders(protected_text, protected_map)

    return fallback_text, f"{first_engine}->fallback", "suspicious", ", ".join(reasons)


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

    translated_text, translation_engine, translation_quality, translation_issue_reason = smart_translate_to_korean(
        text=original_text,
        category=category,
        matched_keywords=matched_keywords,
        glossary=GLOSSARY,
    )

    collected_at_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    created_at_kst = message.created_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")

    row = [
        collected_at_kst,                                 # A
        str(message.guild.name) if message.guild else "", # B
        str(message.channel.name),                        # C
        str(message.author),                              # D
        original_text,                                    # E
        translated_text,                                  # F
        language_code,                                    # G
        category,                                         # H
        ", ".join(matched_keywords),                      # I
        msg_link,                                         # J
        translation_engine,                               # K
        translation_quality,                              # L
        translation_issue_reason,                         # M
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
            translation_engine=translation_engine,
            translation_quality=translation_quality,
            translation_issue_reason=translation_issue_reason,
            created_at=created_at_kst,
            collected_at=collected_at_kst,
        )

        append_to_sheet(row)
        print("✅ DB + 시트 저장 완료:", row)

    except Exception as e:
        print("❌ 저장 실패:", repr(e))

client.run(DISCORD_TOKEN)
