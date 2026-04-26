"""
Threads 로그인 세션을 1회 저장하는 스크립트.

사용법:
    python scripts/threads_login.py

동작:
    1. 크로미움 창이 열림 (headful)
    2. 본인이 직접 Instagram 계정으로 Threads 로그인
    3. 메인 피드가 보이면 터미널로 돌아와서 Enter
    4. .auth/threads_state.json 에 쿠키/스토리지 저장됨
    5. 이후 threads_poster.py 가 이 파일을 읽어 자동 로그인 상태로 동작
"""

import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
AUTH_DIR = BASE_DIR / ".auth"
STATE_FILE = AUTH_DIR / "threads_state.json"
THREADS_URL = "https://www.threads.net/"


async def main() -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(locale="ko-KR")
        page = await context.new_page()
        await page.goto(THREADS_URL)

        print()
        print("=" * 60)
        print("1) 열린 크로미움 창에서 Instagram 계정으로 로그인하세요.")
        print("2) 메인 피드가 보이면 이 터미널로 돌아와서 Enter 키를 누르세요.")
        print("=" * 60)
        print()

        # 터미널 입력 대기 (비동기 asyncio 환경에서 blocking input 사용)
        await asyncio.get_event_loop().run_in_executor(None, input, "로그인 완료 후 Enter: ")

        # 저장 전 로그인 상태 검증
        print("로그인 상태 확인 중...")
        await page.goto(THREADS_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        logged_out_markers = [
            'input[name="username"]',
            'text=/Instagram으로 계속/i',
            'text=/Continue with Instagram/i',
            'text=/Threads에 로그인 또는 가입/i',
            'text=/Log in or sign up for Threads/i',
        ]
        is_logged_out = False
        for marker in logged_out_markers:
            try:
                if await page.locator(marker).first.is_visible(timeout=1000):
                    is_logged_out = True
                    break
            except Exception:
                continue

        if is_logged_out:
            print()
            print("⚠️  아직 비로그인 상태로 보입니다 (로그인 버튼/모달 감지됨).")
            print("   브라우저에서 Instagram 로그인을 완료하고 메인 피드가 보이면,")
            ans = await asyncio.get_event_loop().run_in_executor(
                None, input, "   다시 Enter를 누르세요. (저장 포기하려면 n 입력): "
            )
            if ans.strip().lower() == "n":
                print("❌ 저장 취소됨.")
                await browser.close()
                return
            # 한 번 더 확인
            await page.goto(THREADS_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

        await context.storage_state(path=str(STATE_FILE))
        print(f"✅ 세션 저장 완료: {STATE_FILE}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
