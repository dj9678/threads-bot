# threads-bot

텔레그램으로 ICT 트레이딩 노트/차트 → **Threads 자동 게시** 봇.
Playwright로 저장된 Instagram 세션을 사용. 이미지만 첨부하면 `style-guide.md` 적용된 초안까지 자동 생성.

## 전체 흐름

```
[텔레그램에 노트/차트 전송]
         ↓
[telegram-notes/, screenshots/ 자동 저장]
         ↓
[차트 이미지면 → claude CLI 가 style-guide 기반 초안 생성 → 텔레그램에 답장]
         ↓
[/post <본문>  으로 게시 명령]
         ↓
[Playwright 가 Threads 작성창에 본문+이미지 채움]
         ↓
[사용자가 게시 버튼 클릭 (local 모드) 또는 /confirm 으로 봇이 클릭 (remote 모드)]
         ↓
[/posted 로 소스 노트/이미지 → posted/ 이동]
```

## 폴더 구조

```
threads-bot/
├── scripts/
│   ├── telegram_listener.py    # 봇 엔트리 — 모든 텔레그램 핸들러
│   ├── threads_login.py        # 1회 로그인 (수동) → .auth/threads_state.json
│   ├── threads_poster.py       # Playwright 작성창 채움
│   └── threads_whoami.py       # 저장된 세션의 IG 계정 확인
├── style-guide.md              # 글쓰기 톤/포맷/제약 (500자, 관찰자 시점 등)
├── ict-glossary.md             # ICT 용어 정리 (claude 초안 생성 시 참조)
├── cowork-task.md              # 원래 Claude in Chrome 기반 설계 (참고용)
├── run_bot.bat / stop_bot.bat  # 백그라운드 실행/종료
├── .env.example                # 환경변수 예시
│
├── (gitignore)
├── .env                        # BOT_TOKEN, CHAT_ID
├── .auth/threads_state.json    # Playwright 로그인 세션
├── telegram-notes/             # 받은 텍스트 노트
├── screenshots/                # 받은 차트 이미지
├── posted/                     # /posted 로 정리된 발행 완료 파일
└── logs/                       # 런타임 로그
```

## 셋업

### 1. 텔레그램 봇 생성
1. @BotFather → `/newbot`
2. 토큰 받기
3. @userinfobot 으로 본인 user_id (= chat_id) 확인

### 2. `.env` 작성
```
copy .env.example .env
notepad .env
```
```
BOT_TOKEN=BotFather_토큰
CHAT_ID=본인_chat_id
```

### 3. 첫 실행 (`.venv` 자동 생성됨)
```
.\run_bot.bat
```
- `python -m venv .venv` + `pip install python-telegram-bot python-dotenv playwright`
- `playwright install chromium`
- 백그라운드(pythonw)로 실행, `bot.pid` 기록

### 4. Threads 로그인 1회
```
.\.venv\Scripts\python.exe scripts\threads_login.py
```
- 크로미움 창이 뜸 → Instagram 으로 Threads 로그인 → 메인 피드 보일 때 터미널로 와서 Enter
- `.auth/threads_state.json` 에 세션 저장됨 (이후 자동 사용)

### 5. 검증
```
.\.venv\Scripts\python.exe scripts\threads_whoami.py
```
→ `✅ 로그인된 계정: @your_handle`

## 텔레그램 명령어

| 명령 | 동작 |
|---|---|
| `/whoami` | 저장된 세션의 IG 계정 확인 |
| `/mode` | 현재 모드 표시 |
| `/mode local` | 헤드풀 — 크로미움 창 뜸, 사용자가 "게시" 직접 클릭 |
| `/mode remote` | 헤드리스 + 2단계 승인 (밖에서 폰만으로 게시 가능, 기본값) |
| `/post <본문>` | 작성창에 본문 채움. 줄바꿈 보존. |
| `/confirm` | (remote) 봇이 게시 버튼 클릭 |
| `/cancel` | (remote) 작성 취소 (15분 미응답 시 자동) |
| `/posted` | 직전 /post 의 소스 노트+이미지를 `posted/` 로 이동 (`YYYYMMDD_` prefix) |

## 자동 초안 생성

**이미지를 텔레그램에 보내면** 봇이 자동으로:
1. `screenshots/<ts>.jpg` 저장 (캡션 있으면 `<ts>.txt` 함께)
2. `claude -p` 호출 (`default` 권한 모드 — 읽기만, 안전)
   - `style-guide.md`, `ict-glossary.md`, 차트 이미지를 읽음
   - Threads 포스트 본문 코드블록으로 응답
3. 텔레그램에 초안 송신
4. 사용자가 코드블록 복사 → `/post <본문>` 로 게시 진행

**필요 조건**: `claude` CLI 가 PATH에 있고 `claude login` 완료 상태여야 함.

## 30분 윈도우 자동 첨부

`/post` 시:
- 최근 30분 이내 받은 `screenshots/*.jpg` 중 가장 최신 → 자동 첨부
- 최근 60분 이내 `telegram-notes/*.txt` 가장 최신 → 정리(`/posted`) 대상으로 기록

## remote 모드 2단계 승인 흐름

```
[사용자] /post BTC 15m, ...
[봇]    ⏳ 작성창 채우는 중 (headless)...
[봇]    (작성창 스크린샷 + caption "이대로 게시? /confirm 또는 /cancel")
[사용자] /confirm
[봇]    ⏳ '게시' 클릭 중...
[봇]    ✅ 게시 완료!
[사용자] /posted
[봇]    ✅ 정리 완료 → posted/
```

15분 내 미응답 시 자동 취소.

## 의존성

- Python 3.10+
- `python-telegram-bot==21.6`
- `python-dotenv`
- `playwright` (+ `playwright install chromium`)

## 보안 / 주의

- `.env`, `.auth/`, `logs/`, `bot.pid`, 사용자 콘텐츠 폴더(`telegram-notes/`, `screenshots/`, `posted/`)는 모두 `.gitignore` 처리 — 커밋 금지
- Threads 자동화는 Meta 정책상 회색 영역. 본인 계정 위험 감수. 봇 탐지 회피 시도 X
- `default` 권한 모드로 claude 호출 → Bash/Write/Edit 차단, 안전

## 관련 봇

- `claude-telegram-bridge` — 텔레그램에서 claude CLI 원격제어. 이 봇의 로그/초안에 자연어 분석 가능
- `mcp-ict-trading` — ICT 시그널 분석. 시그널 → 노트 → 이 봇 게시 흐름
