"""
텔레그램 봇 메시지 자동 수신 스크립트 (.env 버전)

저장 경로:
  C:\\Users\\DS\\Documents\\Project\\threads-bot\\scripts\\telegram_listener.py

.env 파일은 프로젝트 루트에 위치해야 함:
  C:\\Users\\DS\\Documents\\Project\\threads-bot\\.env

.env 예시:
  BOT_TOKEN=7891234567:AAH...
  CHAT_ID=123456789

사용법:
  1) pip install python-telegram-bot==21.6 python-dotenv
  2) python telegram_listener.py
"""

import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ========== 경로 설정 ==========
# 이 스크립트는 scripts/ 폴더 안에 있다고 가정
# 프로젝트 루트 = 스크립트의 부모의 부모
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

NOTES_DIR = BASE_DIR / "telegram-notes"
SHOTS_DIR = BASE_DIR / "screenshots"
POSTED_DIR = BASE_DIR / "posted"
LOGS_DIR = BASE_DIR / "logs"
NOTES_DIR.mkdir(parents=True, exist_ok=True)
SHOTS_DIR.mkdir(parents=True, exist_ok=True)
POSTED_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _save_last_output(text: str, kind: str = "output") -> None:
    """bridge 가 'threads-bot 마지막 응답' 분석할 때 참조 — logs/last_output.txt 갱신."""
    try:
        ts = datetime.now().isoformat(timespec="seconds")
        body = f"[{ts}] [{kind}]\n{text}\n"
        (LOGS_DIR / "last_output.txt").write_text(body, encoding="utf-8")
        if kind == "draft":
            (LOGS_DIR / "last_draft.txt").write_text(body, encoding="utf-8")
    except Exception:
        pass

# ========== .env 로드 ==========
if not ENV_PATH.exists():
    raise FileNotFoundError(
        f".env 파일을 찾을 수 없습니다: {ENV_PATH}\n"
        f"해당 위치에 BOT_TOKEN과 CHAT_ID를 포함한 .env 파일을 만들어주세요."
    )

load_dotenv(ENV_PATH)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID_RAW = os.getenv("CHAT_ID")

if not BOT_TOKEN:
    raise ValueError(".env 에 BOT_TOKEN 이 설정되지 않았습니다.")
if not CHAT_ID_RAW:
    raise ValueError(".env 에 CHAT_ID 가 설정되지 않았습니다.")

try:
    CHAT_ID = int(CHAT_ID_RAW)
except ValueError:
    raise ValueError(f"CHAT_ID 는 숫자여야 합니다. 현재 값: {CHAT_ID_RAW}")


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# /post 시 이 창 안에 저장된 스샷/노트가 있으면 자동 첨부/정리 대상
RECENT_IMAGE_WINDOW_MIN = 30
RECENT_NOTE_WINDOW_MIN = 60

# /post 호출 시 소스 파일을 기록 → /posted 에서 posted/ 로 이동
_last_post_sources: "dict[str, Path | None]" = {"note": None, "image": None}

# 작성 모드: "remote" (headless + 2단계 승인, 기본) 또는 "local" (headful + 수동 게시)
_state: dict = {"mode": "remote"}


def _latest_recent_file(
    directory: Path, window_min: int, suffixes: tuple[str, ...]
) -> Path | None:
    """window_min 분 이내에 저장된 파일 중 최신 파일 경로 (루트만, 하위폴더 제외)."""
    if not directory.exists():
        return None
    now = datetime.now().timestamp()
    cutoff = now - window_min * 60
    candidates = []
    for p in directory.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in suffixes:
            continue
        mtime = p.stat().st_mtime
        if mtime >= cutoff:
            candidates.append((mtime, p))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _latest_recent_image() -> Path | None:
    return _latest_recent_file(
        SHOTS_DIR, RECENT_IMAGE_WINDOW_MIN, (".jpg", ".jpeg", ".png", ".webp")
    )


# ──────────────────────────────────────────────────────────────────────
# 자동 초안 페르소나 — claude --append-system-prompt 로 전달됨
# (style-guide.md 의 포맷·길이·제약은 별도. 여기서는 "누가 쓰는가"만)
# ──────────────────────────────────────────────────────────────────────
DRAFT_PERSONA = """너는 10년 차 트레이더야. 글쓰기 좋아해서 본인 차트 분석
노트를 스레드에 풀어 공유한 게 쌓여서 100만 팔로워 모았어. 공감과 신뢰로
팬덤 쌓아왔지.

글쓰기 원칙:
- 반말, 친근하지만 가볍지 않게 (전문성 유지)
- 1인칭 경험 ("오늘 BTC 보다가 눈에 띈 게 있어", "어제 숏 잡았는데 ...")
- AI 티 나는 표현 금지: "~해야 합니다" 강의체, "결론적으로", 과도한 부사("매우/정말로/확실히")
- 짧은 문장 위주, 한 호흡씩
- 결론 또는 핵심 관찰 먼저 → 그 다음 근거
- 차트에 명확히 보이는 사실만, 추측 시 "느낌상" / "감" 같은 표현 사용
- 본인 포지션/생각 솔직하게 공유 OK ("나는 여기서 지켜보는 중")
"""


def _latest_recent_note() -> Path | None:
    return _latest_recent_file(NOTES_DIR, RECENT_NOTE_WINDOW_MIN, (".txt",))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return  # 본인만 허용
    text = update.message.text or ""
    fname = NOTES_DIR / f"{timestamp()}.txt"
    fname.write_text(text, encoding="utf-8")
    await update.message.reply_text(f"✅ 저장됨: {fname.name}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    ts = timestamp()
    photo = update.message.photo[-1]  # 가장 고해상도
    file = await context.bot.get_file(photo.file_id)
    img_path = SHOTS_DIR / f"{ts}.jpg"
    await file.download_to_drive(img_path)

    caption = update.message.caption or ""
    if caption:
        (SHOTS_DIR / f"{ts}.txt").write_text(caption, encoding="utf-8")

    reply_msg = f"📸 저장됨: {img_path.name}"
    if caption:
        reply_msg += f"\n📝 캡션 함께 저장됨"
    await update.message.reply_text(reply_msg)

    # 자동 초안 생성 (style-guide 기반) — 백그라운드 태스크
    asyncio.create_task(_generate_draft(update, img_path, caption))


async def _generate_draft(update: Update, img_path: Path, caption: str) -> None:
    """이미지 + style-guide 로 Threads 포스트 초안 자동 생성.

    claude CLI 를 default 모드(읽기 전용)로 호출 → Bash/Write/Edit 차단,
    Read 만 사용해서 이미지 + style-guide.md 읽고 본문 작성.
    """
    style_guide = BASE_DIR / "style-guide.md"
    glossary = BASE_DIR / "ict-glossary.md"

    notice = await update.message.reply_text("✍️ 초안 생성 중... (10~30초)")

    prompt = (
        f"이미지 (트레이딩 차트): {img_path.resolve()}\n"
        f"스타일 가이드: {style_guide.resolve()}\n"
        f"ICT 용어집: {glossary.resolve()}\n"
        f"사용자 캡션: {caption or '(없음)'}\n\n"
        "위 차트 이미지 + style-guide.md + ict-glossary.md 를 Read 도구로 읽고, "
        "style-guide.md 의 모든 규칙 (길이·이모지·해시태그·금기 표현 등) 을 정확히 "
        "따르는 Threads 포스트 본문을 작성해줘.\n\n"
        "출력: 본문만 ``` 코드블록으로 감싸서. 다른 설명/머리말 없이.\n"
    )

    cmd = [
        "claude",
        "-p", prompt,
        "--append-system-prompt", DRAFT_PERSONA,
        "--permission-mode", "default",
        "--max-turns", "5",
        "--add-dir", str(BASE_DIR.resolve()),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(BASE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        await update.message.reply_text("⏰ 초안 생성 타임아웃 (3분)")
        return
    except FileNotFoundError:
        await update.message.reply_text("❌ claude CLI 못 찾음. PATH 확인 필요.")
        return
    except Exception as e:
        await update.message.reply_text(f"❌ 초안 생성 실패: {type(e).__name__}: {e}")
        return

    text = (stdout or b"").decode("utf-8", errors="replace").strip()
    rc = proc.returncode or 0

    if rc != 0 or not text:
        err = (stderr or b"").decode("utf-8", errors="replace")[:300]
        await update.message.reply_text(
            f"❌ 초안 생성 실패 (rc={rc})\n{err if err else '(빈 응답)'}"
        )
        return

    # bridge 봇이 "이 초안 어때?" 분석할 때 참조
    _save_last_output(text, kind="draft")

    msg = (
        "✍️ 초안 (style-guide 적용):\n\n"
        f"{text}\n\n"
        "사용하려면 위 코드블록 본문을 복사해서 `/post <본문>` 으로 보내세요."
    )
    # 너무 길면 분할 송신
    MAX = 3500
    if len(msg) <= MAX:
        await update.message.reply_text(msg)
    else:
        for i in range(0, len(msg), MAX):
            await update.message.reply_text(msg[i:i + MAX])


async def handle_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return

    # 줄바꿈 보존을 위해 원본 메시지에서 /post 프리픽스만 제거
    raw = update.message.text or ""
    body = re.sub(r"^/post(@\w+)?\s*", "", raw, count=1).strip()

    if not body:
        notes = sorted(NOTES_DIR.glob("*.txt"))
        preview = (
            notes[-1].read_text(encoding="utf-8")[:300] if notes else "(저장된 노트 없음)"
        )
        await update.message.reply_text(
            "사용법:\n"
            "  /post 본문    ← 해당 본문을 Threads 작성창에 채움 (게시는 수동)\n"
            "\n"
            "[최신 저장 노트 프리뷰]\n"
            f"{preview}"
        )
        return

    mode = _state["mode"]

    # remote 모드에서 이미 대기 중인 작성이 있으면 거부
    if mode == "remote":
        try:
            from threads_poster import pending_active  # lazy import
            if pending_active():
                await update.message.reply_text(
                    "⚠️ 이전 /post 가 대기 중입니다.\n"
                    "먼저 /confirm 또는 /cancel 하세요."
                )
                return
        except Exception:
            pass

    image_path = _latest_recent_image()
    note_path = _latest_recent_note()

    # /posted 가 정리 대상으로 쓸 수 있도록 기록
    _last_post_sources["note"] = note_path
    _last_post_sources["image"] = image_path

    image_note = (
        f"🖼️ 이미지 첨부: {image_path.name}"
        if image_path
        else f"🖼️ 이미지 첨부: 없음"
    )
    note_info = (
        f"📝 연결 노트: {note_path.name}"
        if note_path
        else "📝 연결 노트: 없음"
    )
    mode_banner = (
        "🌐 remote (headless + 2단계 승인)"
        if mode == "remote"
        else "🖥️ local (headful + 창에서 직접 게시)"
    )
    await update.message.reply_text(
        f"⏳ Threads 작성창 채우는 중... [{mode_banner}]\n\n"
        f"본문 ({len(body)}자):\n{body[:200]}"
        + ("..." if len(body) > 200 else "")
        + f"\n\n{image_note}\n{note_info}"
    )

    try:
        if mode == "local":
            # 헤드풀, 사용자가 창에서 '게시' 직접 클릭
            from threads_poster import post_text  # lazy import

            async def _notify_filled():
                msg = "✅ 작성창 채움. 크로미움 창에서 '게시' 직접 클릭하세요."
                if image_path is not None:
                    msg += f"\n(이미지 {image_path.name} 첨부됨)"
                msg += "\n게시 후 /posted 로 소스 정리"
                await update.message.reply_text(msg)

            await post_text(
                body,
                headless=False,
                on_filled=_notify_filled,
                image_path=image_path,
            )
            return

        # remote 모드 (default): 헤드리스 + 프리뷰 스크린샷 + /confirm
        from threads_poster import fill_for_approval  # lazy import

        preview_png = await fill_for_approval(
            body, image_path=image_path, headless=True
        )

        caption = (
            "이 내용으로 게시하시겠습니까?\n"
            "  /confirm — 게시\n"
            "  /cancel  — 취소\n"
            f"(15분 내 미응답 시 자동 취소)"
        )
        with open(preview_png, "rb") as f:
            await update.message.reply_photo(photo=f, caption=caption)

        # 15분 후 자동 취소 태스크
        async def _auto_cancel():
            await asyncio.sleep(15 * 60)
            try:
                from threads_poster import pending_active, cancel_pending
                if pending_active():
                    await cancel_pending()
                    await update.message.reply_text(
                        "⏰ 15분 경과로 자동 취소됨."
                    )
            except Exception:
                pass

        asyncio.create_task(_auto_cancel())

    except Exception as e:
        await update.message.reply_text(f"❌ 실패: {type(e).__name__}\n{e}")
        # 실패 직전 저장된 디버그 스크린샷을 함께 전송
        debug_dir = BASE_DIR / "screenshots" / "debug"
        if debug_dir.exists():
            pngs = sorted(debug_dir.glob("*.png"), key=lambda p: p.stat().st_mtime)
            if pngs:
                try:
                    with open(pngs[-1], "rb") as f:
                        await update.message.reply_photo(
                            photo=f, caption=f"debug: {pngs[-1].name}"
                        )
                except Exception as send_err:
                    await update.message.reply_text(
                        f"(디버그 스크린샷 전송 실패: {send_err})"
                    )


async def handle_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    current = _state["mode"]
    arg = (context.args[0].lower() if context.args else "").strip()

    if not arg:
        await update.message.reply_text(
            f"현재 모드: {current}\n\n"
            "  /mode remote  — 헤드리스 + 폰으로 /confirm 승인 (밖에서 권장)\n"
            "  /mode local   — 헤드풀, 크로미움 창에서 직접 '게시' 클릭 (집에서)\n"
        )
        return

    if arg not in ("remote", "local"):
        await update.message.reply_text("값은 'remote' 또는 'local' 만 가능합니다.")
        return

    if arg == current:
        await update.message.reply_text(f"이미 {current} 모드입니다.")
        return

    # remote에서 pending 상태면 전환 막기
    if current == "remote":
        try:
            from threads_poster import pending_active
            if pending_active():
                await update.message.reply_text(
                    "⚠️ 대기 중인 /post 가 있어 모드 전환 불가.\n"
                    "먼저 /confirm 또는 /cancel 하세요."
                )
                return
        except Exception:
            pass

    _state["mode"] = arg
    await update.message.reply_text(f"✅ 모드 변경: {current} → {arg}")


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    try:
        from threads_poster import pending_active, publish_pending
    except Exception as e:
        await update.message.reply_text(f"❌ 모듈 로드 실패: {e}")
        return
    if not pending_active():
        await update.message.reply_text("대기 중인 /post 가 없습니다.")
        return
    await update.message.reply_text("⏳ '게시' 클릭 중...")
    try:
        await publish_pending()
        await update.message.reply_text(
            "✅ 게시 완료!\n"
            "소스 파일 정리하려면 /posted"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ 게시 실패: {type(e).__name__}\n{e}")


async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    try:
        from threads_poster import pending_active, cancel_pending
    except Exception as e:
        await update.message.reply_text(f"❌ 모듈 로드 실패: {e}")
        return
    if not pending_active():
        await update.message.reply_text("대기 중인 /post 가 없습니다.")
        return
    await cancel_pending()
    # /posted 로 정리하지 않도록 소스 기록도 클리어
    _last_post_sources["note"] = None
    _last_post_sources["image"] = None
    await update.message.reply_text("✋ 취소됨. 브라우저 종료, 소스 기록 초기화.")


async def handle_posted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """직전 /post 의 소스 파일들을 posted/ 로 이동 (오늘 날짜 prefix 추가)."""
    if update.effective_chat.id != CHAT_ID:
        return

    note = _last_post_sources.get("note")
    image = _last_post_sources.get("image")

    if not note and not image:
        await update.message.reply_text(
            "정리할 소스가 없습니다.\n"
            "`/post` 로 작성을 먼저 한 뒤 `/posted` 로 정리하세요.\n"
            "(이전에 이미 정리했거나 봇이 재시작되어 기록이 사라진 경우도 포함)"
        )
        return

    prefix = datetime.now().strftime("%Y%m%d")
    POSTED_DIR.mkdir(parents=True, exist_ok=True)
    moved_lines: list[str] = []
    missing: list[str] = []

    def _move(src: Path, label: str) -> None:
        if not src.exists():
            missing.append(f"{label}: {src.name} (이미 없음)")
            return
        dest = POSTED_DIR / f"{prefix}_{src.name}"
        # 이름 충돌 시 (_2, _3...) 서픽스
        i = 2
        while dest.exists():
            dest = POSTED_DIR / f"{prefix}_{src.stem}_{i}{src.suffix}"
            i += 1
        src.replace(dest)
        moved_lines.append(f"{label}: {dest.name}")

    if note:
        _move(note, "note")
    if image:
        _move(image, "image")
        # 이미지에 딸린 캡션 txt 같이 이동
        caption = image.with_suffix(".txt")
        if caption.exists():
            _move(caption, "caption")

    # 기록 초기화 (중복 /posted 방지)
    _last_post_sources["note"] = None
    _last_post_sources["image"] = None

    lines = []
    if moved_lines:
        lines.append("✅ 정리 완료 → posted/")
        lines.extend(f"  {m}" for m in moved_lines)
    if missing:
        lines.append("\n⚠️ 일부 파일 누락:")
        lines.extend(f"  {m}" for m in missing)
    await update.message.reply_text("\n".join(lines) if lines else "정리할 것 없음.")


async def handle_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    await update.message.reply_text("🔍 저장된 세션 확인 중...")
    try:
        from threads_whoami import whoami, STATE_FILE  # lazy import

        if not STATE_FILE.exists():
            await update.message.reply_text(
                "❌ 세션 없음. `scripts/threads_login.py` 먼저 실행하세요."
            )
            return
        username, user_id, valid = await whoami()
        if not valid:
            await update.message.reply_text(
                "⚠️ 세션 만료. threads_login.py 다시 실행 필요.\n"
                f"(만료 전 user ID: {user_id})"
            )
            return
        lines = []
        if username:
            lines.append(f"✅ 로그인된 계정: @{username}")
            lines.append(f"https://www.threads.net/@{username}")
        else:
            lines.append("⚠️ 사용자명 추출 실패 (UI 변경 가능성)")
        if user_id:
            lines.append(f"IG user ID: {user_id}")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"❌ 실패: {type(e).__name__}\n{e}")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("mode", handle_mode))
    app.add_handler(CommandHandler("post", handle_post))
    app.add_handler(CommandHandler("confirm", handle_confirm))
    app.add_handler(CommandHandler("cancel", handle_cancel))
    app.add_handler(CommandHandler("posted", handle_posted))
    app.add_handler(CommandHandler("whoami", handle_whoami))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    print(f"🤖 리스너 시작됨")
    print(f"📁 프로젝트 루트: {BASE_DIR}")
    print(f"📝 메모 저장 위치: {NOTES_DIR}")
    print(f"📸 이미지 저장 위치: {SHOTS_DIR}")
    print(f"👤 허용 Chat ID: {CHAT_ID}")
    print(f"\n텔레그램 봇에 메시지를 보내보세요. Ctrl+C로 종료.\n")
    app.run_polling()


if __name__ == "__main__":
    main()
