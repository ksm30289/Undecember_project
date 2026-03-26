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

# 긍정 키워드 예시
POSITIVE_KEYWORDS = [
    "재밌", "재미", "좋다", "호평", "만족", "잘했", "잘함", "갓겜", "혜자", "갓패치",
    "추천", "굿", "나이스", "지렸", "인정", "잘하", "개추", "장점", "좋네",
    "최고", "훌륭", "개선됐다", "편해졌다", "맘에 든다", "할만", "괜찮", "성공적"
]

KST = ZoneInfo("Asia/Seoul")
TARGET_DATE = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")

SPREADSHEET_ID = "1F2Smu5Z3JbQ5s693meQhHybMhB5vGRLTWKW81uGKgdY"
SHEET_NAME = "언디셈버_KR_긍정 동향"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

MAX_PAGES = 3  # 필요 시 늘려도 됨

# =====================
# Google 인증
# =====================

creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
CREDS = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
service = build("sheets", "v4", credentials=CREDS)


# =====================
# 시트 유틸
# =====================

def ensure_sheet_exists(service, spreadsheet_id, sheet_name):
    metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing_sheets = [s["properties"]["title"] for s in metadata.get("sheets", [])]

    if sheet_name in existing_sheets:
        print(f"✅ 시트 '{sheet_name}' 이미 존재")
        return

    body = {
        "requests": [
            {
                "addSheet": {
                    "properties": {
                        "title": sheet_name
                    }
                }
            }
        ]
    }
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body=body
    ).execute()
    print(f"📄 시트 '{sheet_name}' 생성 완료")


def ensure_header_exists(service, spreadsheet_id, sheet_name):
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:F1"
    ).execute()

    values = result.get("values", [])
    if values:
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
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": header}
    ).execute()

    print("✅ 헤더 생성 완료")


def get_existing_links(service, spreadsheet_id, sheet_name):
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!D:D"
    ).execute()

    values = result.get("values", [])
    links = set()

    for row in values[1:]:  # 헤더 제외
        if row and row[0].startswith("http"):
            links.add(row[0])

    print(f"🔎 기존 링크 {len(links)}건 확인")
    return links


def append_to_google_sheets(data, spreadsheet_id, sheet_name):
    if not data:
        print("🔍 추가할 게시글이 없습니다.")
        return

    sheet = service.spreadsheets()
    sheet.values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A:F",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": data}
    ).execute()

    print(f"✅ Google Sheets 추가 완료 ({len(data)}건)")


# =====================
# 크롤링
# =====================

def get_positive_posts_by_date(target_date, keywords):
    collected = []

    for page in range(1, MAX_PAGES + 1):
        print(f"🔄 {page}페이지 요청 중...")
        url = f"https://gall.dcinside.com/mgallery/board/lists/?id={GALLERY_ID}&page={page}"

        res = requests.get(url, headers=HEADERS, timeout=15)
        res.raise_for_status()

        soup = BeautifulSoup(res.text, "html.parser")
        rows = soup.select("tr.ub-content")

        print(f"📋 게시글 {len(rows)}개 발견")

        if not rows:
            break

        for row in rows:
            title_tag = row.select_one('a[href*="/mgallery/board/view"]')
            date_tag = row.select_one("td.gall_date")

            if not title_tag or not date_tag:
                continue

            raw_date = date_tag.get("title") or date_tag.get_text(strip=True)
            if not raw_date:
                continue

            date_str = raw_date[:10]
            if date_str != target_date:
                continue

            title = title_tag.get_text(strip=True)
            link = "https://gall.dcinside.com" + title_tag["href"]

            try:
                detail_res = requests.get(link, headers=HEADERS, timeout=15)
                detail_res.raise_for_status()

                detail_soup = BeautifulSoup(detail_res.text, "html.parser")
                content_tag = detail_soup.select_one("div.write_div")

                if not content_tag:
                    print(f"⚠️ 본문 없음: {link}")
                    continue

                body = content_tag.get_text(separator=" ", strip=True)
                full_text = f"{title} {body}".lower()

                matched = [kw for kw in keywords if kw.lower() in full_text]
                if not matched:
                    continue

                summary = body[:150].replace("\n", " ").strip()

                print(f"✅ 긍정 키워드 포함: {title}")
                collected.append([
                    datetime.now(KST).strftime("%Y-%m-%d"),  # 수집일자
                    date_str,                               # 게시글작성일
                    title,
                    link,
                    ", ".join(matched),
                    summary
                ])

                time.sleep(0.5)

            except Exception as e:
                print(f"❌ 본문 파싱 실패: {link} / {e}")

    return collected


# =====================
# 실행
# =====================

if __name__ == "__main__":
    print("🎯 대상 날짜:", TARGET_DATE)

    ensure_sheet_exists(service, SPREADSHEET_ID, SHEET_NAME)
    ensure_header_exists(service, SPREADSHEET_ID, SHEET_NAME)

    existing_links = get_existing_links(service, SPREADSHEET_ID, SHEET_NAME)
    posts = get_positive_posts_by_date(TARGET_DATE, POSITIVE_KEYWORDS)

    new_posts = [row for row in posts if row[3] not in existing_links]

    print(f"🆕 신규 게시글 {len(new_posts)}건")
    append_to_google_sheets(new_posts, SPREADSHEET_ID, SHEET_NAME)
