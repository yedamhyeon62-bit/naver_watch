"""
네이버예약 취소표 알림 (ntfy 푸시)

사용법
  pip install playwright requests && playwright install chromium
  1) 먼저 점검:  python naver_watch.py --debug
  2) 감시 시작:  python naver_watch.py

주의: 네이버예약 화면/응답 구조를 직접 확인하지 못하고 짠 1차 버전입니다.
--debug 결과(debug/ 폴더의 JSON, 콘솔 출력)를 보고 available_in()을 맞춰야 할 수 있어요.
"""
import argparse
import json
import os
import pathlib
import random
import time

import requests
from playwright.sync_api import sync_playwright

# ===== 설정 =====
TARGET_DATES = ["2026-12-25"]      # 12/25 체크인 1박이면 25일 슬롯만 보면 됨
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "내가-정한-비밀-이름")  # 영문/숫자/하이픈으로 남이 추측 못 할 이름으로 바꾸세요
CHECK_EVERY = 600                  # 초 (10분). 너무 짧게 하면 차단될 수 있어요
HOTELS = {
    "키스멧 코티지": "https://naver.me/5wrupUqR",
    "오이코스테이": "https://naver.me/5yhjzjyx",
    "평창포레스트": "https://naver.me/FwGeV5aB",
    "음악이흐르는숲": "https://naver.me/5ECu9PEK",
}
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def notify(title_ascii, body, click_url):
    """ntfy 앱으로 푸시. 헤더는 ASCII만 가능해서 한글은 body에."""
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={"Title": title_ascii, "Click": click_url, "Priority": "high"},
            timeout=10,
        )
    except Exception as e:
        print("알림 전송 실패:", e)


def walk(obj):
    """JSON 안의 모든 dict를 순회."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def available_in(data):
    """추정 로직: 대상 날짜가 들어간 dict에서 판매일이고 재고가 남았는지 확인.
    네이버예약 스케줄 응답의 필드명(stock, bookingCount, isSaleDay 등)을 가정한 것."""
    hits = []
    for d in walk(data):
        date = next(
            (v[:10] for v in d.values() if isinstance(v, str) and v[:10] in TARGET_DATES),
            None,
        )
        if not date:
            continue
        stock = d.get("stock")
        booked = d.get("bookingCount") or 0
        sale = d.get("isSaleDay", d.get("isUnitSaleDay", True))
        if sale and isinstance(stock, (int, float)) and stock - booked > 0:
            hits.append(date)
    return hits


def check(browser, name, url, debug):
    ctx = browser.new_context(user_agent=MOBILE_UA, viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    payloads = []

    def on_response(resp):
        try:
            if "json" in (resp.headers.get("content-type") or ""):
                payloads.append((resp.url, resp.json()))
        except Exception:
            pass

    page.on("response", on_response)
    page.goto(url, wait_until="networkidle", timeout=60000)
    # 달력/객실 데이터가 버튼을 눌러야 로드되는 경우를 대비한 시도
    for label in ("예약", "객실", "상품"):
        try:
            page.get_by_text(label, exact=False).first.click(timeout=2000)
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

    final_url = page.url
    found = []
    for i, (u, data) in enumerate(payloads):
        if debug:
            out = pathlib.Path("debug")
            out.mkdir(exist_ok=True)
            (out / f"{name}_{i}.json").write_text(
                json.dumps({"url": u, "data": data}, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            print(f"  [{name}] JSON 응답: {u}")
        found += available_in(data)
    if debug:
        page.screenshot(path=f"debug/{name}.png", full_page=True)
    ctx.close()
    return sorted(set(found)), final_url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true", help="1회 실행하고 응답/스크린샷 저장")
    ap.add_argument("--once", action="store_true", help="1회만 확인하고 종료 (클라우드 실행용)")
    args = ap.parse_args()

    notified = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        while True:
            for name, url in HOTELS.items():
                try:
                    found, final_url = check(browser, name, url, args.debug)
                except Exception as e:
                    print(f"[{name}] 확인 실패: {e}")
                    continue
                print(time.strftime("%H:%M:%S"), name, "가능:" if found else "없음", found)
                if found and name not in notified:
                    notify("Cancellation found", f"{name} {', '.join(found)} 빈 객실 발견! 지금 예약하세요", final_url)
                    notified.add(name)
                if not found:
                    notified.discard(name)  # 다시 마감되면 다음 취소표 때 또 알림
            if args.debug or args.once:
                break
            time.sleep(CHECK_EVERY + random.randint(0, 60))


if __name__ == "__main__":
    main()
