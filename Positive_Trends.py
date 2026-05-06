import os
import json
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from requests.exceptions import ConnectTimeout, ReadTimeout, RequestException
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# =====================
# 설정
# =====================

GALLERY_ID = "undecember"

# 긍정 키워드 예시
POSITIVE_KEYWORDS = [
    "이건 잘했", "기대할게", "기대해봐", "일좀 했네", "만족", "잘했네", "잘함", "갓겜", "혜자", "갓패치",
    "추천", "굿", "나이스", "지렸", "인정", "잘하", "개추", "장점", "좋네", "진짜 재미",
    "최고", "훌륭", "개선됐다", "편해졌다", "맘에 든다", "이쁘", "성공적", "이쁨"
]

KST = ZoneInfo("Asia/Seoul")
TARGET_DATE = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")

SPREADSHEET_ID = "1F2Smu5Z3JbQ5s693meQhHybMhB5vGRLTWKW81uGKgdY"
SHEET_NAME = "언디셈버_KR_긍정 동향"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://gall.dcinside.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

MAX_PAGES = 3           # 필요 시 늘려도 됨
REQUEST_TIMEOUT = 30    # 기존 15 -> 30
REQUEST_RETRIES = 3     # 재시도 횟수
REQUEST_SLEEP = 0.8     # 요청 간 대기시간

# =====================
# Google 인증
# =====================

creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
CREDS = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
service = build("sheets", "v4", credentials=CREDS)


# =====================
# 공통 요청 함수
# =====================

def fetch(url, headers=None, retries=REQUEST_RETRIES, timeout=REQUEST_TIMEOUT):
    """
    requests.get 공통 래퍼
    - 타임아웃/일시적 네트워크 오류 재시도
    - 최종 실패 시 None 반환
    """
    for attempt in range(1, retries + 1):
        try:
            print(f"🌐 요청 시도 ({attempt}/{retries}): {url}")
            res = requests.get(url, headers=headers, timeout=timeout)
            res.raise_for_status()
            return res

        except (ConnectTimeout, ReadTimeout):
            print(f"⏰ 타임아웃 ({attempt}/{retries}): {url}")

        except RequestException as e:
            print(f"❌ 요청 실패 ({attempt}/{retries}): {url} / {e}")

        if attempt < retries:
            sleep_sec = 2 * attempt
            print(f"⏳ {sleep_sec}초 후 재시도")
            time.sleep(sleep_sec)

    print(f"🚫 최종 요청 실패: {url}")
    return None


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
        print("✅ 헤더 이미 존재")
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

        res = fetch(url, headers=HEADERS)
        if res is None:
            print(f"⚠️ 리스트 페이지 스킵: {url}")
            continue

        soup = BeautifulSoup(res.text, "html.parser")
        rows = soup.select("tr.ub-content")

        print(f"📋 게시글 {len(rows)}개 발견")

        if not rows:
            print("📭 게시글이 없어 종료")
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
            href = title_tag.get("href", "").strip()
            if not href:
                continue

            link = "https://gall.dcinside.com" + href

            try:
                time.sleep(REQUEST_SLEEP)

                detail_res = fetch(link, headers=HEADERS)
                if detail_res is None:
                    print(f"⚠️ 상세 페이지 스킵: {link}")
                    continue

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

            except Exception as e:
                print(f"❌ 본문 파싱 실패: {link} / {e}")

    return collected


# =====================
# 실행
# =====================

if __name__ == "__main__":
    print("🎯 대상 날짜:", TARGET_DATE)

    try:
        ensure_sheet_exists(service, SPREADSHEET_ID, SHEET_NAME)
        ensure_header_exists(service, SPREADSHEET_ID, SHEET_NAME)

        existing_links = get_existing_links(service, SPREADSHEET_ID, SHEET_NAME)
        posts = get_positive_posts_by_date(TARGET_DATE, POSITIVE_KEYWORDS)

        new_posts = [row for row in posts if row[3] not in existing_links]

        print(f"🆕 신규 게시글 {len(new_posts)}건")
        append_to_google_sheets(new_posts, SPREADSHEET_ID, SHEET_NAME)

        print("🏁 작업 완료")

    except Exception as e:
        print(f"💥 전체 실행 실패: {e}")
