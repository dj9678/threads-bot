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
    """모달의 contenteditable 영역에 본문 입력 (첫 포스트)."""
    # 모달이 뜰 시간
    await page.wait_for_timeout(1000)

    composer = page.locator('div[contenteditable="true"]').first
    await composer.wait_for(state="visible", timeout=5000)
    await composer.click()
    # type은 한 글자씩 입력 — 한글/이모지 안전
    await composer.type(text, delay=10)


async def _click_add_to_thread(page: Page) -> None:
    """'Add to thread' / '스레드에 추가' 클릭 후 새 입력창 등장 대기."""
    before_count = await page.locator('div[contenteditable="true"]').count()

    name_pattern = re.compile(r"스레드에 추가|Add to thread", re.IGNORECASE)
    attempts = [
        ("role=button", lambda: page.get_by_role("button", name=name_pattern).first),
        ("text", lambda: page.get_by_text(name_pattern).first),
        ("aria-label-ko", lambda: page.locator('[aria-label="스레드에 추가"]').first),
        ("aria-label-en", lambda: page.locator('[aria-label="Add to thread"]').first),
    ]

    tried: list[str] = []
    for tag, fn in attempts:
        try:
            btn = fn()
            await btn.wait_for(state="visible", timeout=2500)
            await btn.click(timeout=2500)
            print(f"[add_to_thread] success via: {tag}")
            # 새 입력창 등장 대기
            for _ in range(20):
                await page.wait_for_timeout(150)
                if await page.locator('div[contenteditable="true"]').count() > before_count:
                    return
            return
        except Exception as e:
            tried.append(f"{tag}: {type(e).__name__}")
            continue

    debug_png = await _save_debug(page, "add_to_thread_not_found")
    raise RuntimeError(
        "'스레드에 추가/Add to thread' 버튼을 찾지 못했습니다. 시도:\n  "
        + "\n  ".join(tried)
        + f"\n\n디버그 스크린샷: {debug_png}"
    )


async def _fill_chain(
    page: Page,
    posts: list[str],
    image_path: "Path | None" = None,
    image_index: int = -1,
) -> None:
    """본문 + 이어쓰기 시퀀스를 작성 모달에 채운다.

    image_path가 있으면 image_index 위치 포스트에 첨부.
    image_index < 0 또는 범위 밖이면 마지막 포스트.
    이미지 첨부는 해당 포스트의 입력창이 활성화된 직후 수행.
    """
    if not posts:
        raise ValueError("posts가 비어있음")

    # 이미지 첨부 대상 포스트 인덱스 정규화
    if image_path is not None:
        if image_index < 0 or image_index >= len(posts):
            image_index = len(posts) - 1
    else:
        image_index = -1  # 첨부 안함

    # 첫 포스트
    await _fill_text(page, posts[0])
    if image_index == 0:
        await _attach_image(page, image_path, input_index=image_index)
    await page.wait_for_timeout(800)

    # 이어쓰기 — Threads 서버측 상태 동기화 대기 시간 충분히 확보
    for i, post in enumerate(posts[1:], start=1):
        await _click_add_to_thread(page)
        await page.wait_for_timeout(600)  # Add to thread 후 새 composer 안정화
        composer = page.locator('div[contenteditable="true"]').nth(i)
        await composer.wait_for(state="visible", timeout=5000)
        await composer.click()
        # 타이핑 속도를 좀 늦춰서 server-side 입력 추적 누락 방지
        await composer.type(post, delay=18)
        if i == image_index:
            await page.wait_for_timeout(300)
            await _attach_image(page, image_path, input_index=image_index)
        # 다음 Add to thread 전 / 마지막 composer 후 충분히 대기
        await page.wait_for_timeout(900)

    # 게시 직전 추가 안정화 — 마지막 composer 텍스트가 서버에 반영될 시간
    await page.wait_for_timeout(1500)


async def _attach_image(page: Page, image_path: Path, input_index: int = -1) -> None:
    """작성 모달에 이미지 첨부.

    input_index: 멀티 포스트에서 input[type=file] 중 몇 번째를 쓸지.
                 -1이면 마지막(현재 활성 포스트로 추정).

    전략:
    1) DOM에 존재하는 input[type=file]에 직접 set_input_files
    2) 실패 시 '사진 추가' 버튼 클릭 + filechooser 이벤트로 파일 전달
    """
    image_path = image_path.resolve()
    if not image_path.exists():
        raise FileNotFoundError(f"이미지 파일 없음: {image_path}")

    # 전략 1: 숨겨진 input[type=file] 직접 사용
    try:
        count = await page.locator('input[type="file"]').count()
        if count > 0:
            idx = count - 1 if input_index < 0 or input_index >= count else input_index
            file_input = page.locator('input[type="file"]').nth(idx)
            await file_input.set_input_files(str(image_path))
            # 업로드 프리뷰 렌더 대기
            await page.wait_for_timeout(1500)
            print(f"[attach_image] success via hidden input[type=file] (idx={idx}/{count})")
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
    """작성 모달 푸터의 '게시' 버튼만 클릭.

    주의: 페이지 다른 곳에도 '게시' 텍스트가 있을 수 있으므로
          반드시 dialog/modal 안의 버튼으로 좁힌다.
    """
    # 게시 직전 상태 진단: contenteditable 개수
    try:
        cnt = await page.locator('div[contenteditable="true"]').count()
        print(f"[click_publish] composers in modal: {cnt}")
    except Exception:
        pass

    name_pattern = re.compile(r"^(게시|Post)$", re.IGNORECASE)

    # 모달 컨테이너 후보 — Threads는 role=dialog 사용
    dialog = page.locator('div[role="dialog"]').last

    attempts = [
        ("dialog>role=button", lambda: dialog.get_by_role("button", name=name_pattern).last),
        ("dialog>div[role=button]", lambda: dialog.locator('div[role="button"]', has_text=re.compile(r"^(게시|Post)$")).last),
        # 폴백: 페이지 전체에서 마지막 매칭 (.last로 모달 푸터 버튼 우선 시도)
        ("page>role=button.last", lambda: page.get_by_role("button", name=name_pattern).last),
        ("page>div[role=button].last", lambda: page.locator('div[role="button"]', has_text=re.compile(r"^(게시|Post)$")).last),
    ]

    tried: list[str] = []
    for tag, attempt in attempts:
        try:
            btn = attempt()
            await btn.wait_for(state="visible", timeout=3000)
            # 버튼이 disabled 상태면 스킵
            try:
                disabled = await btn.get_attribute("aria-disabled")
                if disabled == "true":
                    tried.append(f"{tag}: aria-disabled=true")
                    continue
            except Exception:
                pass
            await btn.click(timeout=3000)
            print(f"[click_publish] success via: {tag}")
            return
        except Exception as e:
            tried.append(f"{tag}: {type(e).__name__}: {str(e)[:80]}")
            continue

    debug_png = await _save_debug(page, "publish_btn_not_found")
    raise RuntimeError(
        "'게시' 버튼 클릭 실패. 시도:\n  "
        + "\n  ".join(tried)
        + f"\n\n디버그 스크린샷: {debug_png}"
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
    text: "str | list[str]",
    image_path: "Path | None" = None,
    headless: bool = True,
    image_index: int = -1,
) -> Path:
    """headless 브라우저를 열고 작성창을 채운 뒤 스크린샷 경로를 반환.

    text가 list[str]이면 첫 항목 = 본문, 나머지 = 'Add to thread' 이어쓰기.

    브라우저/페이지는 모듈 레벨 _pending 에 유지되어 publish_pending / cancel_pending 에서 재사용.
    """
    posts = [text] if isinstance(text, str) else list(text)
    if not posts:
        raise ValueError("text가 비어있음")
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
        await _fill_chain(page, posts, image_path=image_path, image_index=image_index)

        # 렌더 안정화 대기 후 미리보기 스크린샷
        await page.wait_for_timeout(1500)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        preview_png = DEBUG_DIR / f"{ts}_preview.png"
        try:
            await page.screenshot(path=str(preview_png), full_page=True)
        except Exception as e:
            print(f"[fill_for_approval] 스크린샷 실패: {e}")

        # 상태 저장
        _pending["playwright"] = pw
        _pending["browser"] = browser
        _pending["context"] = context
        _pending["page"] = page
        _pending["preview_png"] = preview_png
        # 게시될 본문 저장 (리스트는 +++ 로 join 해서 보존)
        _pending["body"] = "\n+++\n".join(posts)

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
        # 게시 직전 상태 스냅샷 (디버그)
        try:
            await _save_debug(page, "before_publish")
        except Exception:
            pass
        await _click_publish(page)
        # 게시 직후 짧은 대기 후 다시 스냅샷
        await page.wait_for_timeout(2000)
        try:
            await _save_debug(page, "after_publish_2s")
        except Exception:
            pass
        await _wait_publish_done(page, timeout_sec=20)
        try:
            await _save_debug(page, "after_publish_done")
        except Exception:
            pass
    finally:
        await _cleanup_pending()


async def cancel_pending() -> None:
    """대기 중인 작성 모달을 게시 없이 종료."""
    if not pending_active():
        return
    await _cleanup_pending()


async def post_text(
    text: "str | list[str]",
    headless: bool = False,
    on_filled=None,
    image_path: "Path | None" = None,
    image_index: int = -1,
) -> str:
    """Threads 작성창을 열고 텍스트를 채운 뒤 (옵션으로 이미지 첨부) 사용자의 게시를 기다린다.

    Args:
        text: 작성창에 입력할 본문 (str이면 단일, list면 본문+이어쓰기 시퀀스)
        headless: 헤드리스 모드 여부
        on_filled: 작성창 채움 완료 시 호출할 async 콜백 (예: 텔레그램 알림)
        image_path: 첨부할 이미지 경로 (옵션, 첫 포스트에만 첨부)

    Returns: "filled" (채움 성공 후 정상 종료)
    """
    posts = [text] if isinstance(text, str) else list(text)
    if not posts:
        raise ValueError("text가 비어있음")
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
        await _fill_chain(page, posts, image_path=image_path, image_index=image_index)

        if image_path is not None:
            print(f"🖼️  이미지 첨부 완료: {image_path.name}")
        if len(posts) > 1:
            print(f"🧵  이어쓰기 {len(posts) - 1}개 추가 완료")

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
    raw = sys.argv[1] if len(sys.argv) > 1 else "테스트 본문"
    # +++ 단독 라인을 구분자로 분리 (CLI에서도 멀티 포스트 테스트 가능)
    parts = re.split(r"^\s*\+\+\+\s*$", raw, flags=re.MULTILINE)
    posts = [p.strip() for p in parts if p.strip()]
    await post_text(posts if len(posts) > 1 else posts[0], headless=False)


if __name__ == "__main__":
    asyncio.run(_cli())
