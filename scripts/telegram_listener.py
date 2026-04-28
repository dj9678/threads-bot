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


def _save_last_posted(body: str, direction: str = "") -> None:
    """게시 완료된 내용 저장 — 다음 초안 생성 시 연속성 참조용."""
    try:
        ts = datetime.now().isoformat(timespec="seconds")
        content = f"[{ts}]\n[direction] {direction}\n[body]\n{body}\n"
        (LOGS_DIR / "last_posted.txt").write_text(content, encoding="utf-8")
    except Exception:
        pass


def _load_last_posted() -> tuple[str, str]:
    """직전 게시글 내용 및 방향 로드. (body, direction) 반환."""
    path = LOGS_DIR / "last_posted.txt"
    if not path.exists():
        return "", ""
    try:
        content = path.read_text(encoding="utf-8")
        direction = ""
        body = ""
        # [direction] 라인 파싱
        for line in content.split("\n"):
            if line.startswith("[direction]"):
                direction = line.replace("[direction]", "").strip()
                break
        # [body] 이후 내용 파싱
        if "[body]" in content:
            body = content.split("[body]", 1)[1].strip()
        return body, direction
    except Exception:
        return "", ""

# ========== .env 로드 ==========
if not ENV_PATH.exists():
    raise FileNotFoundError(
        f".env 파일을 찾을 수 없습니다: {ENV_PATH}\n"
        f"해당 위치에 BOT_TOKEN과 CHAT_ID를 포함한 .env 파일을 만들어주세요."
    )

load_dotenv(ENV_PATH, override=True)

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

# 마지막 초안의 분석 방향 저장 (상승/하락/중립)
_last_draft_direction: dict = {"value": ""}

# 마지막 초안의 본문(코드블록 내용만) 저장 — /post_draft 에서 사용
_last_draft_body: dict = {"value": ""}


def _extract_draft_body(text: str) -> str:
    """초안 응답에서 ``` 코드블록 안쪽 본문만 추출.

    포맷:
        [방향] 상승/하락/중립
        [본문]
        ```
        실제 본문
        ```
    """
    # ``` 로 둘러싸인 첫 블록 추출
    m = re.search(r"```(?:\w+)?\s*\n?(.*?)\n?```", text, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    # 코드블록 없으면 [본문] 이후 텍스트 통째로
    if "[본문]" in text:
        return text.split("[본문]", 1)[1].strip().strip("`").strip()
    return text.strip()


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

    # 코드블록 복붙으로 앞에 공백/줄바꿈이 묻어 명령어 인식 실패한 경우 폴백 라우팅
    stripped = text.lstrip()
    if stripped.startswith("/post_draft"):
        await handle_post_draft(update, context)
        return
    if stripped.startswith("/post"):
        body = re.sub(r"^/post(@\w+)?\s*", "", stripped, count=1).strip()
        await handle_post(update, context, override_body=body)
        return
    if stripped.startswith("/edit"):
        await handle_edit(update, context)
        return
    if stripped.startswith("/confirm"):
        await handle_confirm(update, context)
        return
    if stripped.startswith("/cancel"):
        await handle_cancel(update, context)
        return
    if stripped.startswith("/help") or stripped.startswith("/start"):
        await handle_help(update, context)
        return

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

    직전 게시글이 있으면 연속성을 고려해 작성:
    - 직전 상승 신호 → 이번 하락이면 "지난번 말한 것과 다르게 흘러가네"
    - 강한 방향 확신 대신 반대 상황 가능성도 언급
    """
    style_guide = BASE_DIR / "style-guide.md"
    glossary = BASE_DIR / "ict-glossary.md"

    notice = await update.message.reply_text("✍️ 초안 생성 중... (10~30초)")

    # 직전 게시글 로드
    prev_body, prev_direction = _load_last_posted()
    prev_context = ""
    if prev_body:
        prev_context = (
            f"\n\n[직전 게시글 정보]\n"
            f"방향: {prev_direction or '(명시 안 됨)'}\n"
            f"내용:\n{prev_body[:500]}{'...' if len(prev_body) > 500 else ''}\n"
        )

    # style-guide.md에 "신중한 표현 & 확신 자제", "직전 게시글과 연결" 섹션 있음
    # 여기서는 직전 게시글 정보만 주입하고, 세부 규칙은 style-guide 참조하도록 유도
    continuity_instruction = """
[직전 게시글 연결 지침]
style-guide.md의 "직전 게시글과 연결" 섹션 규칙을 따를 것.
직전 게시글 정보가 위에 있으면 방향 변화에 맞춰 자연스럽게 연결.
"""

    prompt = (
        f"이미지 (트레이딩 차트): {img_path.resolve()}\n"
        f"스타일 가이드: {style_guide.resolve()}\n"
        f"ICT 용어집: {glossary.resolve()}\n"
        f"사용자 캡션: {caption or '(없음)'}\n"
        f"{prev_context}\n"
        f"{continuity_instruction}\n\n"
        "**차트 시그널 해석 규칙 (매우 중요):**\n"
        "- T 캔들 라벨의 화살표 방향에 절대 주의: ↑(위 화살표) = 상승/롱 시그널, ↓(아래 화살표) = 하락/숏 시그널.\n"
        "  T2F↑/T3F↑/T2↑ 는 모두 롱(상승), T2F↓/T3F↓/T2↓/T4↓ 는 모두 숏(하락).\n"
        "- 화살표 식별이 애매하면 추측하지 말 것. '양방향 시그널 공존', '방향 확정 짓기 애매함' 으로 처리.\n"
        "- 캔들 색깔(녹/적)로 시그널 방향을 추론하지 말 것. 라벨 화살표가 진실의 원천.\n"
        "- **사용자 캡션이 차트 해석과 다르면 캡션을 우선**. 캡션에 방향이 명시되면 그 방향으로 작성.\n\n"
        "위 차트 이미지 + style-guide.md + ict-glossary.md 를 Read 도구로 읽고, "
        "style-guide.md 의 모든 규칙 (길이·이모지·해시태그·금기 표현 등) 을 정확히 "
        "따르는 **멀티 포스트(스레드 체인)** 형식으로 작성해줘.\n\n"
        "**멀티 포스트 구조 (style-guide.md '멀티 포스트 작성 패턴' 섹션 참고):**\n"
        "- 1번 (본문/Hook): 짧게(200~300자). 호기심 유발 + 핵심 결론 한 줄.\n"
        "  style-guide.md의 '훅 작성법' 섹션 패턴 따를 것.\n"
        "- 2번 (이어쓰기 1): 본격 분석/설명 (300~500자). 차트 구조·시그널·근거.\n"
        "  여기에 [IMG] 한 줄을 적당한 위치에 넣어 차트 이미지 첨부 위치 표시.\n"
        "- 3번 (이어쓰기 2, 선택): 시나리오/관점 추가 (300~500자). 양방향 열어두기.\n"
        "- 마지막 (마무리): 짧게(150~250자). 정리 + (필요시) 본인 포지션/관망 멘트.\n\n"
        "포스트 사이는 반드시 한 줄에 `+++` 만 적은 구분자로 분리할 것.\n"
        "총 3~4개 포스트 권장.\n\n"
        "**중요 출력 규칙:**\n"
        "- 분석 과정/관찰 노트/차트 설명 같은 사전 텍스트를 출력하지 마.\n"
        "- '차트를 분석해볼게', '**차트 분석:**', '**핵심 관찰:**' 류 머리말 절대 금지.\n"
        "- 마크다운 불릿(- ...)으로 차트 분석을 나열하지 마.\n"
        "- 출력은 아래 블록 **하나만**:\n\n"
        "[방향] 상승 또는 하락 또는 중립\n"
        "[본문]\n"
        "```\n"
        "1번 본문 (Hook)\n"
        "+++\n"
        "2번 이어쓰기 (설명 + [IMG])\n"
        "+++\n"
        "3번 이어쓰기 (선택)\n"
        "+++\n"
        "마지막 마무리\n"
        "```\n\n"
        "이 블록 외엔 어떤 텍스트도 출력하지 말 것."
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

    # 응답 앞에 분석 과정/머리말이 붙어 나오면 [방향] 라인부터 잘라냄
    direction_pos = text.find("[방향]")
    if direction_pos > 0:
        text = text[direction_pos:].strip()

    # 방향 파싱 ([방향] 상승/하락/중립)
    direction_match = re.search(r"\[방향\]\s*(상승|하락|중립)", text)
    if direction_match:
        _last_draft_direction["value"] = direction_match.group(1)
    else:
        # 없으면 본문에서 키워드로 추론 시도
        if "상승" in text or "롱" in text.lower() or "반등" in text:
            _last_draft_direction["value"] = "상승"
        elif "하락" in text or "숏" in text.lower() or "눌림" in text or "하방" in text:
            _last_draft_direction["value"] = "하락"
        else:
            _last_draft_direction["value"] = "중립"

    # bridge 봇이 "이 초안 어때?" 분석할 때 참조
    _save_last_output(text, kind="draft")

    # 본문만 추출해서 저장 (/post_draft 에서 사용)
    extracted = _extract_draft_body(text)
    _last_draft_body["value"] = extracted

    msg = (
        "✍️ 초안 (style-guide 적용):\n\n"
        f"{text}\n\n"
        f"📊 분석 방향: {_last_draft_direction['value']}\n\n"
        "👉 그대로 게시: /post_draft\n"
        "✏️ 수정 후 게시: /edit (코드블록 복사 → 편집 → 전송)"
    )
    # 너무 길면 분할 송신
    MAX = 3500
    if len(msg) <= MAX:
        await update.message.reply_text(msg)
    else:
        for i in range(0, len(msg), MAX):
            await update.message.reply_text(msg[i:i + MAX])


async def handle_post(update: Update, context: ContextTypes.DEFAULT_TYPE, override_body: "str | None" = None):
    if update.effective_chat.id != CHAT_ID:
        return

    if override_body is not None:
        body = override_body.strip()
    else:
        # 줄바꿈 보존을 위해 원본 메시지에서 /post 프리픽스만 제거
        raw = update.message.text or ""
        # 앞쪽 공백/줄바꿈 허용해서 /post 프리픽스 제거 (코드블록 복붙 대응)
        body = re.sub(r"^\s*/post(@\w+)?\s*", "", raw, count=1).strip()

    if not body:
        notes = sorted(NOTES_DIR.glob("*.txt"))
        preview = (
            notes[-1].read_text(encoding="utf-8")[:300] if notes else "(저장된 노트 없음)"
        )
        await update.message.reply_text(
            "사용법:\n"
            "  /post 본문    ← 해당 본문을 Threads 작성창에 채움 (게시는 수동)\n"
            "\n"
            "  멀티 포스트 (Add to thread):\n"
            "    한 줄에 +++ 만 적어서 분리\n"
            "    예) /post 본문\\n+++\\n이어글1\\n+++\\n이어글2\n"
            "\n"
            "  이미지 첨부 위치 지정:\n"
            "    원하는 포스트 안에 [IMG] 한 줄 추가 (마커 줄은 제거됨)\n"
            "    미지정 시 마지막 포스트에 첨부\n"
            "\n"
            "[최신 저장 노트 프리뷰]\n"
            f"{preview}"
        )
        return

    # +++ 단독 라인을 구분자로 분리 → 멀티 포스트
    parts = re.split(r"^\s*\+\+\+\s*$", body, flags=re.MULTILINE)
    posts = [p.strip() for p in parts if p.strip()]
    if not posts:
        await update.message.reply_text("본문이 비어있습니다.")
        return

    # [IMG] 마커가 있는 포스트 인덱스 찾고 마커 줄 제거
    # 마커 없으면 image_index = -1 (= 마지막 포스트, threads_poster 측 기본값)
    image_index = -1
    cleaned_posts: list[str] = []
    for idx, p in enumerate(posts):
        # 줄 단위로 보고 [IMG] 만 있는 라인 제거
        lines = p.split("\n")
        has_marker = False
        new_lines = []
        for line in lines:
            if re.fullmatch(r"\s*\[IMG\]\s*", line):
                has_marker = True
                continue
            new_lines.append(line)
        if has_marker and image_index == -1:
            image_index = idx
        cleaned_posts.append("\n".join(new_lines).strip())
    posts = [p for p in cleaned_posts if p]
    if not posts:
        await update.message.reply_text("본문이 비어있습니다.")
        return

    # 단일 포스트면 str, 멀티면 list 로 전달
    body_arg = posts[0] if len(posts) == 1 else posts

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

    if image_path:
        if len(posts) > 1:
            target_idx = image_index if image_index >= 0 else len(posts) - 1
            target_label = (
                f"본문(1번)" if target_idx == 0 else f"이어쓰기 {target_idx}번"
            )
            image_note = f"🖼️ 이미지 첨부: {image_path.name} → {target_label}"
        else:
            image_note = f"🖼️ 이미지 첨부: {image_path.name}"
    else:
        image_note = "🖼️ 이미지 첨부: 없음"
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
    chain_info = (
        f"🧵 멀티 포스트: 본문 + 이어쓰기 {len(posts) - 1}개"
        if len(posts) > 1
        else "📝 단일 포스트"
    )
    first_preview = posts[0]
    await update.message.reply_text(
        f"⏳ Threads 작성창 채우는 중... [{mode_banner}]\n\n"
        f"{chain_info}\n"
        f"첫 포스트 ({len(first_preview)}자):\n{first_preview[:200]}"
        + ("..." if len(first_preview) > 200 else "")
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
                body_arg,
                headless=False,
                on_filled=_notify_filled,
                image_path=image_path,
                image_index=image_index,
            )
            return

        # remote 모드 (default): 헤드리스 + 프리뷰 스크린샷 + /confirm
        from threads_poster import fill_for_approval  # lazy import

        preview_png = await fill_for_approval(
            body_arg, image_path=image_path, headless=True, image_index=image_index
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


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    msg = (
        "📖 명령어 목록\n\n"
        "▪️ /whoami — 로그인된 IG 계정 확인\n"
        "▪️ /mode — 모드 보기 (local / remote 전환)\n"
        "\n"
        "📝 게시 흐름\n"
        "▪️ /post <본문> — 본문 게시 (`+++` 구분자로 멀티 포스트, `[IMG]` 마커로 이미지 위치)\n"
        "▪️ /post_draft — 직전 자동 초안 그대로 게시\n"
        "▪️ /edit — 직전 초안을 코드블록으로 받기 (복사·편집·재전송)\n"
        "▪️ /confirm — (remote) 대기 게시 확정 [현재 비권장: Threads 감지]\n"
        "▪️ /cancel — (remote) 대기 게시 취소\n"
        "▪️ /posted — 직전 게시 소스 파일을 posted/ 로 이동\n"
        "\n"
        "💬 일반 입력\n"
        "▪️ 텍스트 → telegram-notes/ 저장\n"
        "▪️ 차트 이미지(+캡션) → screenshots/ 저장 + 자동 초안 생성\n"
        "\n"
        "💡 팁\n"
        "• 차트 캡션에 방향 한 단어(상승/하락/중립) 적으면 정확도 ↑\n"
        "• 자동 게시는 현재 비권장 — 봇은 초안 생성용, 게시는 직접"
    )
    await update.message.reply_text(msg)


async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """직전 초안 본문을 `/post <본문>` 형태로 반환.
    사용자가 코드블록을 길게 눌러 복사 → 편집 → 전송하면 그대로 게시 흐름 진입.
    """
    if update.effective_chat.id != CHAT_ID:
        return

    body = _last_draft_body.get("value", "").strip()
    if not body:
        await update.message.reply_text(
            "직전 초안이 없습니다.\n"
            "차트 이미지를 먼저 보내서 초안을 생성하세요."
        )
        return

    full_cmd = f"/post {body}"

    # 텔레그램 메시지 길이 제한 (4096) 고려
    MAX = 3800
    intro = (
        "✏️ 편집용 초안입니다.\n"
        "아래 코드블록을 길게 눌러 복사 → 입력창에 붙여넣고 수정 → 전송하면 게시됩니다.\n"
    )

    if len(full_cmd) <= MAX:
        # MarkdownV2 대신 간단하게: ``` 펜스로 감싸서 모노스페이스 + 복사 용이
        msg = intro + f"\n```\n{full_cmd}\n```"
        try:
            await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception:
            # 마크다운 이스케이프 충돌 시 평문 폴백
            await update.message.reply_text(intro + "\n" + full_cmd)
    else:
        # 너무 길면 분할 송신 (편집 흐름엔 불리하지만 폴백)
        await update.message.reply_text(
            intro + "\n(본문이 너무 길어 분할 전송. 합쳐서 한 번에 /post 로 보내세요.)"
        )
        for i in range(0, len(full_cmd), MAX):
            await update.message.reply_text(full_cmd[i:i + MAX])


async def handle_post_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """직전에 자동 생성된 초안 본문을 그대로 /post 흐름으로 보낸다."""
    if update.effective_chat.id != CHAT_ID:
        return

    body = _last_draft_body.get("value", "").strip()
    if not body:
        await update.message.reply_text(
            "직전 초안이 없습니다.\n"
            "차트 이미지를 먼저 보내서 초안을 생성하세요."
        )
        return

    await handle_post(update, context, override_body=body)


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
        from threads_poster import pending_active, publish_pending, get_pending_body
    except Exception as e:
        await update.message.reply_text(f"❌ 모듈 로드 실패: {e}")
        return
    if not pending_active():
        await update.message.reply_text("대기 중인 /post 가 없습니다.")
        return

    # 게시 전에 본문 가져오기 (publish_pending 후에는 초기화됨)
    posted_body = get_pending_body() or ""

    await update.message.reply_text("⏳ '게시' 클릭 중...")
    try:
        await publish_pending()

        # 게시 완료된 내용 저장 (다음 초안 생성 시 연속성 참조용)
        # 방향은 초안 저장 시 함께 저장됨
        direction = _last_draft_direction.get("value", "")
        _save_last_posted(posted_body, direction)

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
    app.add_handler(CommandHandler("post_draft", handle_post_draft))
    app.add_handler(CommandHandler("edit", handle_edit))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("start", handle_help))
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
