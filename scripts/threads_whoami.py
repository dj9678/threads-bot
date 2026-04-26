"""
저장된 Threads 세션으로 현재 로그인된 계정을 확인한다.

사용법:
    python scripts/threads_whoami.py

출력 예:
    ✅ 로그인된 계정: @myhandle
       Instagram user ID: 12345678
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = BASE_DIR / ".auth" / "threads_state.json"
THREADS_URL = "https://www.threads.net/"


def _extract_ds_user_id() -> Optional[str]:
    """세션 JSON에서 ds_user_id 쿠키(=인스타그램 숫자 ID) 추출."""
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    for cookie in data.get("cookies", []):
        if cookie.get("name") == "ds_user_id":
            return cookie.get("value")
    return None


async def whoami() -> Tuple[Optional[str], Optional[str], bool]:
    """저장된 세션의 사용자명/ID 반환.

    Returns:
        (username, user_id, session_valid)
        - username: "@" 없이 순수 handle, 추출 실패 시 None
        - user_id:  ds_user_id 값 (세션 파일만 있으면 추출 가능)
        - session_valid: 로그인 폼이 뜨지 않았는지 (True=유효, False=만료)
    """
    user_id = _extract_ds_user_id()

    if not STATE_FILE.exists():
        return None, None, False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(STATE_FILE),
            locale="ko-KR",
        )
        page = await context.new_page()
        await page.goto(THREADS_URL, wait_until="domcontentloaded")

        # 로그인 폼이 뜨면 세션 만료
        try:
            await page.wait_for_selector('input[name="username"]', timeout=2000)
            await browser.close()
            return None, user_id, False
        except PWTimeout:
            pass

        # UI 렌더 대기
        await page.wait_for_timeout(2000)

        # 프로필 링크 후보 탐색
        username: Optional[str] = None
        candidates = [
            'a[aria-label="프로필"]',
            'a[aria-label="Profile"]',
            'a[role="link"][href^="/@"]',
            'a[href^="/@"]',
        ]
        for selector in candidates:
            try:
                links = page.locator(selector)
                count = await links.count()
                for i in range(min(count, 20)):
                    href = await links.nth(i).get_attribute("href")
                    if href and href.startswith("/@") and len(href) > 2:
                        # "/@user" or "/@user/replies" 등
                        handle = href.split("/")[1][1:]
                        if handle:
                            username = handle
                            break
                if username:
                    break
            except Exception:
                continue

        await browser.close()
        return username, user_id, True


async def main() -> None:
    if not STATE_FILE.exists():
        print(f"❌ 세션 파일 없음: {STATE_FILE}")
        print("   먼저 `python scripts/threads_login.py` 를 실행해서 로그인하세요.")
        return

    print(f"📄 세션 파일: {STATE_FILE}")
    username, user_id, valid = await whoami()

    if not valid:
        print("⚠️ 로그인 세션이 만료되었습니다. threads_login.py 를 다시 실행하세요.")
        if user_id:
            print(f"   (만료 전 마지막 user ID: {user_id})")
        return

    if user_id:
        print(f"🔢 Instagram user ID: {user_id}")
    if username:
        print(f"✅ 로그인된 계정: @{username}")
        print(f"   프로필: https://www.threads.net/@{username}")
    else:
        print("⚠️ 사용자명 추출 실패 — UI 셀렉터가 바뀌었을 수 있습니다.")
        print("   세션은 유효해 보이니 threads_poster.py 로는 동작할 수 있습니다.")


if __name__ == "__main__":
    asyncio.run(main())
