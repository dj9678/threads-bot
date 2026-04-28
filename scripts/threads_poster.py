"""
저장된 Threads 세션으로 작성창을 열고 본문을 채운다.
**"게시" 버튼은 자동으로 누르지 않는다** — 사용자가 직접 클릭.

사용 방식:
    from threads_poster import post_text
    await post_text("올릴 본문")

CLI 테스트:
    python scripts/threads_poster.py "테스트 본문"
"""

import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = BASE_DIR / ".auth" / "threads_state.json"
DEBUG_DIR = BASE_DIR / "screenshots" / "debug"
THREADS_URL = "https://www.threads.net/"

# 사용자가 게시 버튼을 누를 때까지 브라우저 유지 시간
KEEP_OPEN_SECONDS = 10 * 60


async def _save_debug(page: Page, tag: str) -> Path:
    """현재 페이지 스크린샷 + URL/제목 저장. 반환: 스크린샷 경로."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    png = DEBUG_DIR / f"{ts}_{tag}.png"
    info = DEBUG_DIR / f"{ts}_{tag}.txt"
    try:
        await page.screenshot(path=str(png), full_page=True)
    except Exception as e:
        png.write_text(f"screenshot failed: {e}")
    try:
        url = page.url
        title = await page.title()
        body_text = await page.evaluate(
            "() => document.body ? document.body.innerText.slice(0, 2000) : ''"
        )
    except Exception:
        url, title, body_text = "?", "?", ""
    info.write_text(
        f"url: {url}\ntitle: {title}\n\n--- body text (first 2000 chars) ---\n{body_text}\n",
        encoding="utf-8",
    )
    return png


async def _open_composer(page: Page) -> None:
    """Threads 메인 → 작성 모달 열기.

    UI가 계속 바뀌므로 여러 후보를 시도한다.
    """
    # 페이지 렌더 대기 (SPA라 networkidle까지 기다림)
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except PWTimeout:
        pass
    await page.wait_for_timeout(1500)

    name_pattern = re.compile(
        r"스레드 시작|새 스레드|스레드|Start a thread|New thread|Create|만들기|게시물 작성|What's new|무슨 일",
        re.IGNORECASE,
    )

    # 여러 전략을 순차 시도 (하나라도 성공하면 return)
    attempts = [
        ("placeholder", lambda: page.get_by_placeholder(name_pattern).first.click(timeout=2500)),
        ("role=textbox", lambda: page.get_by_role("textbox", name=name_pattern).first.click(timeout=2500)),
        ("role=button", lambda: page.get_by_role("button", name=name_pattern).first.click(timeout=2500)),
        ("role=link", lambda: page.get_by_role("link", name=name_pattern).first.click(timeout=2500)),
        ("aria-label=만들기", lambda: page.locator('[aria-label="만들기"]').first.click(timeout=2500)),
        ("aria-label=Create", lambda: page.locator('[aria-label="Create"]').first.click(timeout=2500)),
        ("aria-label=게시물 작성", lambda: page.locator('[aria-label="게시물 작성"]').first.click(timeout=2500)),
        ("aria-label=새 스레드", lambda: page.locator('[aria-label="새 스레드"]').first.click(timeout=2500)),
        ("aria-label=New thread", lambda: page.locator('[aria-label="New thread"]').first.click(timeout=2500)),
        ("svg-parent-create", lambda: page.locator('svg[aria-label="만들기"]').first.locator('xpath=ancestor::*[self::a or self::button][1]').click(timeout=2500)),
        ("svg-parent-Create", lambda: page.locator('svg[aria-label="Create"]').first.locator('xpath=ancestor::*[self::a or self::button][1]').click(timeout=2500)),
        ("text-스레드시작", lambda: page.get_by_text(re.compile(r"스레드 시작")).first.click(timeout=2500)),
    ]

    tried: list[str] = []
    for tag, fn in attempts:
        try:
            await fn()
            print(f"[open_composer] success via: {tag}")
            return
        except Exception as e:
            tried.append(f"{tag}: {type(e).__name__}")
            continue

    # 전부 실패 → 디버그 저장
    debug_png = await _save_debug(page, "composer_not_found")
    raise RuntimeError(
        "작성 버튼/입력창을 찾지 못했습니다. 시도한 셀렉터:\n  "
        + "\n  ".join(tried)
        + f"\n\n디버그 스크린샷: {debug_png}\n"
        f"동반 텍스트파일에 현재 URL/제목/본문 텍스트 저장됨."
    )


async def _fill_text(page: Page, text: str) -> None:
    """모달의 contenteditable 영역에 본문 입력."""
    # 모달이 뜰 시간
    await page.wait_for_timeout(1000)

    composer = page.locator('div[contenteditable="true"]').first
    await composer.wait_for(state="visible", timeout=5000)
    await composer.click()
    # type은 한 글자씩 입력 — 한글/이모지 안전
    await composer.type(text, delay=10)


async def _attach_image(page: Page, image_path: Path) -> None:
    """작성 모달에 이미지 첨부.

    전략:
    1) DOM에 존재하는 input[type=file]에 직접 set_input_files
    2) 실패 시 '사진 추가' 버튼 클릭 + filechooser 이벤트로 파일 전달
    """
    image_path = image_path.resolve()
    if not image_path.exists():
        raise FileNotFoundError(f"이미지 파일 없음: {image_path}")

    # 전략 1: 숨겨진 input[type=file] 직접 사용
    try:
        file_input = page.locator('input[type="file"]').first
        # 존재 여부 확인 (visible이 아니어도 OK — hidden input 많음)
        count = await page.locator('input[type="file"]').count()
        if count > 0:
            await file_input.set_input_files(str(image_path))
            # 업로드 프리뷰 렌더 대기
            await page.wait_for_timeout(1500)
            print(f"[attach_image] success via hidden input[type=file]")
            return
    except Exception as e:
        print(f"[attach_image] hidden input 실패: {type(e).__name__}: {e}")

    # 전략 2: '첨부/사진 추가' 버튼 클릭 + FileChooser 이벤트
    attach_label_pattern = re.compile(
        r"사진 추가|미디어 추가|첨부|파일 첨부|Attach|Add photo|Add media",
        re.IGNORECASE,
    )
    attach_attempts = [
        lambda: page.get_by_role("button", name=attach_label_pattern).first,
        lambda: page.get_by_label(attach_label_pattern).first,
        lambda: page.locator('svg[aria-label="미디어 첨부"]').first.locator('xpath=ancestor::*[self::button or self::a][1]'),
        lambda: page.locator('svg[aria-label="Attach media"]').first.locator('xpath=ancestor::*[self::button or self::a][1]'),
    ]

    for attempt in attach_attempts:
        try:
            async with page.expect_file_chooser(timeout=3000) as fc_info:
                await attempt().click(timeout=2500)
            chooser = await fc_info.value
            await chooser.set_files(str(image_path))
            await page.wait_for_timeout(1500)
            print("[attach_image] success via filechooser")
            return
        except Exception:
            continue

    debug_png = await _save_debug(page, "attach_image_failed")
    raise RuntimeError(
        f"이미지 첨부 버튼을 찾지 못했습니다. UI가 바뀌었을 수 있음.\n"
        f"디버그 스크린샷: {debug_png}"
    )


async def _click_publish(page: Page) -> None:
    """작성 모달의 '게시' 버튼 클릭."""
    name_pattern = re.compile(r"^(게시|Post)$", re.IGNORECASE)
    attempts = [
        lambda: page.get_by_role("button", name=name_pattern).first,
        lambda: page.locator('div[role="button"]', has_text=re.compile(r"^(게시|Post)$")).first,
    ]
    for attempt in attempts:
        try:
            btn = attempt()
            await btn.wait_for(state="visible", timeout=3000)
            await btn.click(timeout=3000)
            print("[click_publish] success")
            return
        except Exception:
            continue

    debug_png = await _save_debug(page, "publish_btn_not_found")
    raise RuntimeError(
        f"'게시' 버튼을 찾지 못했습니다. 디버그 스크린샷: {debug_png}"
    )


async def _wait_publish_done(page: Page, timeout_sec: int = 20) -> None:
    """'게시' 클릭 후 Threads가 모달을 닫고 피드로 돌아갈 때까지 대기.

    실패해도 치명적이지 않음 (예외 삼키고 그냥 리턴).
    """
    try:
        # 작성 모달이 사라지는 걸 감지
        await page.locator('div[contenteditable="true"]').first.wait_for(
            state="detached", timeout=timeout_sec * 1000
        )
    except PWTimeout:
        pass


# ============================================================
# 2단계 승인용 (모바일 친화) 모듈 레벨 상태
# ============================================================
# handle_post 에서 fill_for_approval 호출 → 브라우저 유지
# handle_confirm → publish_pending 호출 → 게시 + 정리
# handle_cancel → cancel_pending 호출 → 브라우저 종료
_pending: dict = {
    "playwright": None,
    "browser": None,
    "context": None,
    "page": None,
    "preview_png": None,
    "body": None,  # 게시될 본문
}


def pending_active() -> bool:
    return _pending.get("browser") is not None


def get_pending_body() -> str | None:
    """대기 중인 게시 본문 반환."""
    return _pending.get("body")


async def _cleanup_pending() -> None:
    browser = _pending.get("browser")
    pw = _pending.get("playwright")
    try:
        if browser is not None:
            await browser.close()
    except Exception:
        pass
    try:
        if pw is not None:
            await pw.stop()
    except Exception:
        pass
    for k in _pending:
        _pending[k] = None


async def fill_for_approval(
    text: str,
    image_path: "Path | None" = None,
    headless: bool = True,
) -> Path:
    """headless 브라우저를 열고 작성창을 채운 뒤 스크린샷 경로를 반환.

    브라우저/페이지는 모듈 레벨 _pending 에 유지되어 publish_pending / cancel_pending 에서 재사용.
    """
    if pending_active():
        raise RuntimeError("이전 대기 중인 작성이 있습니다. 먼저 confirm/cancel 하세요.")

    if not STATE_FILE.exists():
        raise FileNotFoundError(
            f"세션 파일 없음: {STATE_FILE}\n"
            f"먼저 `python scripts/threads_login.py` 로 로그인하세요."
        )

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            storage_state=str(STATE_FILE),
            locale="ko-KR",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        await page.goto(THREADS_URL, wait_until="domcontentloaded")

        # 로그인 상태 체크
        try:
            await page.wait_for_load_state("networkidle", timeout=6000)
        except PWTimeout:
            pass

        logged_out_markers = [
            'input[name="username"]',
            'text=/Instagram으로 계속/i',
            'text=/Continue with Instagram/i',
            'text=/Threads에 로그인 또는 가입/i',
            'text=/Log in or sign up for Threads/i',
        ]
        for marker in logged_out_markers:
            try:
                if await page.locator(marker).first.is_visible(timeout=1200):
                    await browser.close()
                    await pw.stop()
                    raise RuntimeError(
                        "저장된 세션이 로그인되어 있지 않습니다. "
                        "threads_login.py 를 다시 실행하세요."
                    )
            except PWTimeout:
                continue
            except RuntimeError:
                raise
            except Exception:
                continue

        await _open_composer(page)
        await _fill_text(page, text)

        if image_path is not None:
            await _attach_image(page, image_path)

        # 렌더 안정화 대기 후 미리보기 스크린샷
        await page.wait_for_timeout(1500)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        preview_png = DEBUG_DIR / f"{ts}_preview.png"
        try:
            await page.screenshot(path=str(preview_png), full_page=False)
        except Exception as e:
            print(f"[fill_for_approval] 스크린샷 실패: {e}")

        # 상태 저장
        _pending["playwright"] = pw
        _pending["browser"] = browser
        _pending["context"] = context
        _pending["page"] = page
        _pending["preview_png"] = preview_png
        _pending["body"] = text  # 게시될 본문 저장

        return preview_png
    except Exception:
        # 실패 시 정리
        try:
            await pw.stop()
        except Exception:
            pass
        raise


async def publish_pending() -> None:
    """대기 중인 작성 모달에서 '게시' 클릭 후 종료."""
    page = _pending.get("page")
    if page is None:
        raise RuntimeError("대기 중인 작성이 없습니다.")
    try:
        await _click_publish(page)
        await _wait_publish_done(page, timeout_sec=20)
    finally:
        await _cleanup_pending()


async def cancel_pending() -> None:
    """대기 중인 작성 모달을 게시 없이 종료."""
    if not pending_active():
        return
    await _cleanup_pending()


async def post_text(
    text: str,
    headless: bool = False,
    on_filled=None,
    image_path: "Path | None" = None,
) -> str:
    """Threads 작성창을 열고 텍스트를 채운 뒤 (옵션으로 이미지 첨부) 사용자의 게시를 기다린다.

    Args:
        text: 작성창에 입력할 본문
        headless: 헤드리스 모드 여부
        on_filled: 작성창 채움 완료 시 호출할 async 콜백 (예: 텔레그램 알림)
        image_path: 첨부할 이미지 경로 (옵션)

    Returns: "filled" (채움 성공 후 정상 종료)
    """
    if not STATE_FILE.exists():
        raise FileNotFoundError(
            f"세션 파일 없음: {STATE_FILE}\n"
            f"먼저 `python scripts/threads_login.py` 로 로그인하세요."
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            storage_state=str(STATE_FILE),
            locale="ko-KR",
        )
        page = await context.new_page()
        await page.goto(THREADS_URL, wait_until="domcontentloaded")

        # 로그인 상태 확인 — 로그인 안내 요소가 보이면 세션 비로그인/만료
        try:
            await page.wait_for_load_state("networkidle", timeout=6000)
        except PWTimeout:
            pass

        logged_out_markers = [
            'input[name="username"]',
            'text=/Instagram으로 계속/i',
            'text=/Continue with Instagram/i',
            'text=/Threads에 로그인 또는 가입/i',
            'text=/Log in or sign up for Threads/i',
            'a[href*="/login"]',
        ]
        is_logged_out = False
        for marker in logged_out_markers:
            try:
                loc = page.locator(marker).first
                if await loc.is_visible(timeout=1500):
                    is_logged_out = True
                    break
            except Exception:
                continue

        if is_logged_out:
            debug_png = await _save_debug(page, "not_logged_in")
            await browser.close()
            raise RuntimeError(
                "저장된 세션이 로그인되어 있지 않습니다. "
                "threads_login.py 를 다시 실행해서 Instagram 계정으로 완전히 로그인 후 "
                "메인 피드가 보일 때 Enter 하세요.\n"
                f"디버그 스크린샷: {debug_png}"
            )

        await _open_composer(page)
        await _fill_text(page, text)

        if image_path is not None:
            await _attach_image(page, image_path)
            print(f"🖼️  이미지 첨부 완료: {image_path.name}")

        print("✅ 작성창에 본문 채움. 브라우저에서 '게시' 버튼을 직접 눌러주세요.")
        print(f"   (창은 최대 {KEEP_OPEN_SECONDS // 60}분 후 자동 종료)")

        # 채움 완료 즉시 외부 알림 (텔레그램 등)
        if on_filled is not None:
            try:
                await on_filled()
            except Exception as e:
                print(f"(on_filled callback 실패: {e})")

        # 사용자가 창을 닫거나 타임아웃될 때까지 유지
        closed = asyncio.Event()
        page.once("close", lambda _=None: closed.set())
        try:
            await asyncio.wait_for(closed.wait(), timeout=KEEP_OPEN_SECONDS)
        except asyncio.TimeoutError:
            pass
        try:
            await browser.close()
        except Exception:
            pass
        return "filled"


async def _cli() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else "테스트 본문"
    await post_text(text, headless=False)


if __name__ == "__main__":
    asyncio.run(_cli())
