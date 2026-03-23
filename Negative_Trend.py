import os
import json
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# =====================
# 설정
# =====================

GALLERY_ID = "undecember"

# 🔴 부정 키워드
NEGATIVE_KEYWORDS = [
    "망함", "망했다", "노잼", "재미없", "지루", "쓰레기", "최악",
    "실망", "접는다", "접음", "삭제", "환불", "과금유도", "과금",
    "버그", "렉", "튕김", "크래시", "오류", "에러", "문제",
    "불편", "짜증", "답답", "개판", "운영", "병신", "정신차려",
    "밸런스 붕괴", "너프", "사기", "불공정", "신고", "공정위",
    "밸붕", "지랄", "씨발", "시발", "미친", "개같", "방치",
    "운영자", "안돼", "안됩", "도망", "하지마", "너무하"
]

KST = ZoneInfo("Asia/Seoul")
TARGET_DATE = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")

SPREADSHEET_ID = "1F2Smu5Z3JbQ5s693meQhHybMhB5vGRLTWKW81uGKgdY"
SHEET_NAME = "언디셈버_KR_부정 동향"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

MAX_PAGES = 3

# =====================
# Google 인증
# =====================

creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
CREDS = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
service = build("sheets", "v4", credentials=CREDS)

# =====================
# 시트 유틸
# =====================

def ensure_sheet_exists():
    metadata = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = [s["properties"]["title"] for s in metadata.get("sheets", [])]

    if SHEET_NAME in sheets:
        return

    body = {
        "requests": [
            {"addSheet": {"properties": {"title": SHEET_NAME}}}
        ]
    }

    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body=body
    ).execute()

def ensure_header():
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A1:F1"
    ).execute()

    if result.get("values"):
        return

    header = [[
        "수집일자",
        "게시글작성일",
        "제목",
        "링크",
        "감지된 키워드",
        "본문 요약"
    ]]

    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A1",
        valueInputOption="RAW",
        body={"values": header}
    ).execute()

def get_existing_links():
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!D:D"
    ).execute()

    values = result.get("values", [])
    links = set()

    for row in values[1:]:
        if row and row[0].startswith("http"):
            links.add(row[0])

    return links

def append_rows(rows):
    if not rows:
        print("🔍 신규 데이터 없음")
        return

    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A:F",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows}
    ).execute()

    print(f"✅ 시트 추가 완료 ({len(rows)}건)")

# =====================
# 크롤링
# =====================

def crawl():
    collected = []

    for page in range(1, MAX_PAGES + 1):
        print(f"🔄 {page}페이지")

        url = f"https://gall.dcinside.com/mgallery/board/lists/?id={GALLERY_ID}&page={page}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        rows = soup.select("tr.ub-content")

        if not rows:
            break

        for row in rows:
            title_tag = row.select_one('a[href*="/view"]')
            date_tag = row.select_one("td.gall_date")

            if not title_tag or not date_tag:
                continue

            raw_date = date_tag.get("title") or date_tag.text
            if not raw_date:
                continue

            date_str = raw_date[:10]

            if date_str != TARGET_DATE:
                continue

            title = title_tag.text.strip()
            link = "https://gall.dcinside.com" + title_tag["href"]

            try:
                detail = requests.get(link, headers=HEADERS, timeout=10)
                soup_detail = BeautifulSoup(detail.text, "html.parser")

                content_tag = soup_detail.select_one("div.write_div")
                if not content_tag:
                    continue

                body = content_tag.get_text(" ", strip=True)
                full_text = (title + " " + body).lower()

                matched = [kw for kw in NEGATIVE_KEYWORDS if kw.lower() in full_text]

                if not matched:
                    continue

                summary = body[:150]

                collected.append([
                    datetime.now(KST).strftime("%Y-%m-%d"),
                    date_str,
                    title,
                    link,
                    ", ".join(matched),
                    summary
                ])

                print("❌ 부정 감지:", title)

                time.sleep(0.5)

            except Exception as e:
                print("에러:", e)

    return collected

# =====================
# 실행
# =====================

if __name__ == "__main__":
    print("🎯 날짜:", TARGET_DATE)

    ensure_sheet_exists()
    ensure_header()

    existing = get_existing_links()
    data = crawl()

    new_data = [row for row in data if row[3] not in existing]

    print(f"🆕 신규 {len(new_data)}건")

    append_rows(new_data)
