# Kankan 설치·실행 가이드 (macOS)

Kankan을 처음 설치해 실행하는 사용자를 위한 가이드입니다. macOS 전용이며 Apple Silicon·Intel 모두 동작합니다.
Kankan은 이 Mac의 **Codex**와 **Claude Code** 세션 로그를 읽어 칸반 보드로 보여 주는 로컬 앱입니다.
모델을 호출하지 않으며, 데이터는 프로젝트 폴더의 `Kanban-board/.local/`에 기본 저장됩니다.

## 1. 준비물

| 항목 | 필요 여부 | 확인 / 설치 |
| --- | --- | --- |
| macOS | 필수 | 12 이상 권장 (macOS 26에서 검증) |
| Python 3.9 이상 | 필수 | `python3 --version` · 없으면 `xcode-select --install` (권장, 시스템 Python 3.9 설치) 또는 `brew install python` |
| Codex 또는 Claude Code | 둘 중 하나 이상 | 평소처럼 한 번 이상 사용해 세션 로그가 있어야 카드가 생깁니다 |
| Git | 받는 방법에 따라 | `git --version` (Xcode Command Line Tools에 포함) |
| 추가 패키지 | 없음 | `pip install` 불필요 (표준 라이브러리만 사용) |

Kankan이 읽는 세션 로그 위치(자동 탐색):

- Codex: `~/.codex/sessions`, `~/.codex/archived_sessions`, `$CODEX_HOME/sessions`, Orca의 Codex 계정 폴더
- Claude Code: `~/.claude/projects`, `$CLAUDE_CONFIG_DIR/projects`

`codex`·`claude` 명령 자체는 세션 수집에 필요 없습니다(터미널에서 `kanban run`으로 실행할 때만 필요).

## 2. 받기

**Git으로 받기 (권장)**

```bash
cd ~/Projects            # 원하는 위치
git clone https://github.com/LeeYuHoon/Kanban-board.git
cd Kanban-board
```

**폴더째 복사하기**: 기존 Mac의 `Kanban-board` 폴더를 AirDrop·USB 등으로 복사해도 됩니다.
`.local/` 폴더는 그 Mac의 데이터이므로 빼고 복사하세요(옮기고 싶다면 아래 7번 참고).
브라우저로 내려받은 zip이라 `Kankan.command`가 열리지 않으면 한 번만 실행하세요:

```bash
xattr -dr com.apple.quarantine ~/Projects/Kanban-board
```

## 3. 점검과 실행

터미널에서 **다운로드한 `Kanban-board` 안의 `bin` 폴더로 이동한 뒤** 실행합니다.
앞의 Git 받기 단계에서 `cd Kanban-board`까지 실행했다면:

```bash
cd bin
```

새 터미널을 열었다면 설치 위치부터 입력하세요. 예를 들어 `~/Projects/Kanban-board`에 받았다면:

```bash
cd ~/Projects/Kanban-board/bin
```

다른 위치에 받았다면 위 경로를 실제 위치로 바꾸세요.
터미널에 `cd `를 입력하고(뒤에 공백 포함), Finder에서 `Kanban-board` 안의 **`bin` 폴더**를 터미널로 끌어다 놓은 뒤 Enter를 눌러도 됩니다.

이제 아래 명령을 실행하세요. **`./`는 현재 폴더에 있는 파일을 실행한다는 뜻이므로 생략하지 마세요.**

```bash
./kankan doctor     # 환경 점검: Python, 포트, 세션 로그 폴더, CLI
./kankan start      # 백그라운드로 실행 (터미널을 닫아도 계속 실행)
./kankan open       # 브라우저로 열기 → http://127.0.0.1:4310
```

또는 Finder에서 **`Kankan.command`를 더블클릭**하면 실행 후 브라우저가 열립니다.
처음에 macOS가 막으면 `Kankan.command`를 우클릭 → **열기**를 한 번 누르세요.

실행되면 최근 7일 안의 Codex·Claude Code 세션이 작업 폴더별 프로젝트와 카드로 자동 등록됩니다.
그 뒤로는 평소처럼 Codex·Claude Code를 쓰면 약 5초 안에 반영됩니다. Kankan이 꺼져 있던 동안의 기록은 다시 켜면 따라잡습니다.

## 4. 자주 쓰는 명령

**이 가이드의 모든 `./kankan …` 명령은 `Kanban-board/bin` 폴더에서 실행합니다.**
새 터미널을 열었다면 먼저 `cd`로 해당 폴더로 이동하세요. 아래 표의 명령도 동일합니다.

| 명령 | 설명 |
| --- | --- |
| `./kankan start` | 백그라운드 실행 |
| `./kankan stop` | 종료 |
| `./kankan restart` | 다시 시작 (코드 업데이트 후) |
| `./kankan status` | 실행 여부, 카드 수, 수집 상태 |
| `./kankan open` | 브라우저로 열기 |
| `./kankan logs` | 서버 로그 보기 (Ctrl+C로 나가기) |
| `./kankan backup [파일]` | DB 백업 (실행 중에도 가능) |
| `./kankan doctor` | 환경 점검 |
| `./kankan foreground` | 현재 터미널에서 실행 (Ctrl+C로 종료) |
| `./kankan install-login` | 로그인할 때 자동 실행 |
| `./kankan uninstall-login` | 자동 실행 해제 |

## 5. 로그인할 때 자동 실행 (선택)

Kankan은 **실행 중일 때만** 세션을 수집합니다. 항상 켜 두려면:

```bash
./kankan install-login
```

`~/Library/LaunchAgents/com.kankan.server.plist`를 만들어 로그인할 때 실행하고, 비정상 종료되면 macOS가 다시 띄웁니다.
`./kankan stop`은 이번 로그인 동안만 멈추고, 완전히 끄려면 `./kankan uninstall-login`을 실행하세요.
Kankan 폴더를 옮겼거나 Python을 바꿨다면 `./kankan install-login`을 다시 실행해 경로를 갱신하세요.

## 6. 설정 바꾸기

환경 변수를 명령 앞에 붙입니다(자동 실행 사용 시 `install-login` 실행 때 지정하면 저장됩니다).

```bash
KANKAN_PORT=4311 ./kankan start                          # 다른 포트
KANKAN_PYTHON=/opt/homebrew/bin/python3 ./kankan start   # 특정 Python
KANBAN_DB=~/kankan-data/kanban.sqlite3 ./kankan start     # DB 위치 변경
KANKAN_SESSION_ROOTS='{"codex":["/path/codex/sessions"],"claude":["/path/claude/projects"]}' ./kankan start
```

`KANKAN_SESSION_ROOTS`를 지정하면 기본 탐색 위치 대신 그 위치만 읽습니다.

## 7. 데이터 옮기기 / 백업

데이터는 프로젝트 폴더의 `.local/kanban.sqlite3` 하나입니다(Obsidian 설정·내 설명·선택 저장 포함).

1. 기존 Mac에서 `./kankan backup ~/Desktop/kankan.sqlite3` (실행 중에도 일관된 파일 하나로 저장됩니다)
2. 새 Mac에서 `./kankan stop` 후 그 파일을 프로젝트 폴더의 `.local/kanban.sqlite3`로 복사(`bin` 폴더 기준으로는 `../.local/kanban.sqlite3`)
3. 새 Mac에서 `./kankan start`

카드의 원본 로그 경로는 기존 Mac 기준이라, 새 Mac에서는 새 Mac의 세션만 계속 수집됩니다. 옮기지 않고 새로 시작해도 됩니다.

## 8. 업데이트

`bin` 폴더에서 실행하세요. `git pull`은 저장소 안의 하위 폴더에서도 동작합니다.

```bash
git pull
./kankan restart
```

DB 구조 변경은 시작할 때 자동으로 적용되며 기존 데이터는 유지됩니다.

## 9. Obsidian 연동 (선택)

왼쪽 메뉴의 OBSIDIAN → **연동 설정**에서 프로젝트별로 Vault 경로와 저장 방식(자동 저장 / 선택 저장)을 정합니다.
Vault가 `문서`·`데스크탑`·iCloud Drive 안에 있으면 macOS가 접근 권한을 물을 수 있습니다.
자동 실행(LaunchAgent)으로 쓰는데 Vault에 저장되지 않으면 `시스템 설정 → 개인정보 보호 및 보안 → 파일 및 폴더`(또는 전체 디스크 접근)에서
`./kankan doctor`에 표시된 Python에 권한을 주거나, Vault를 홈 폴더 아래 다른 곳에 두세요. (이 경우는 자동 테스트로 검증하지 못했습니다.)

## 10. 문제 해결

| 증상 | 해결 |
| --- | --- |
| `Python 3.9 이상이 필요합니다` | `xcode-select --install` 후 다시 실행. Homebrew Python은 `KANKAN_PYTHON`으로 지정 |
| `포트 4310를 다른 프로그램이 쓰고 있습니다` | `KANKAN_PORT=4311 ./kankan start` |
| 카드가 하나도 없음 | `./kankan doctor`로 세션 로그 폴더 확인. 최근 7일 안에 Codex·Claude Code를 쓴 기록이 있어야 함. 로그가 다른 곳이면 `KANKAN_SESSION_ROOTS` |
| 브라우저에 `Local access only` | 주소를 `http://127.0.0.1:4310` 또는 `http://localhost:4310`으로 직접 입력 |
| 시작이 안 됨 | `./kankan logs`, `./kankan foreground`로 오류 확인 |
| 원격·클라우드 Codex/Claude 작업이 안 보임 | 이 Mac에 세션 로그가 남는 실행만 수집합니다 |

## 11. 완전 제거

```bash
./kankan uninstall
```

삭제할 항목을 먼저 보여 주고 **`삭제` 또는 `delete`를 입력해야** 진행합니다(스크립트에서는 `--yes`).
Kankan이 만든 것을 모두 지웁니다.

- 실행 중인 서버와 로그인 자동 실행(`~/Library/LaunchAgents/com.kankan.server.plist`, 그 폴더를 Kankan이 만들었다면 폴더도)
- DB 전체: 앱 폴더의 `.local/`(DB·로그·백업·PID) — `KANBAN_DB`로 다른 곳에 두었다면 그 DB 파일과 `-wal`·`-shm`(빈 폴더 포함).
  자동 실행 설정에 저장된 `KANBAN_DB`도 찾아서 지웁니다
- 연결했던 Obsidian Vault의 `Kankan/` 폴더(숨김 폴더·편집본 포함). 노트를 남기려면 `./kankan uninstall --keep-obsidian`
- 앱 폴더 전체(`Kanban-board/`)와, 시스템 Python이 이 앱 경로에 남긴 캐시

제거 후 남는 것은 Kankan의 것이 아닌 항목뿐입니다.

- Codex·Claude Code의 세션 로그와 설정(원래 그 도구들의 파일)
- Obsidian Vault의 다른 노트와 `.obsidian` 설정
- 브라우저에 저장된 화면 설정(선택한 프로젝트·검색 보기 방식): 브라우저 설정에서 `127.0.0.1` 사이트 데이터를 삭제하면 지워집니다

DB를 남기고 싶다면 제거 전에 `./kankan backup ~/Desktop/kankan.sqlite3`로 백업하세요.

## 개발자용: 테스트

테스트는 프로젝트 최상위 폴더에서 실행합니다. `bin` 폴더에 있다면 먼저 `cd ..`로 한 단계 위로 이동하세요.

```bash
python3 -m unittest discover -s tests -v
# 브라우저 시나리오 (Google Chrome 필요, KANKAN_CHROME로 경로 변경 가능)
# 설치한 Google Chrome을 쓰므로 Playwright 브라우저는 내려받지 않습니다(앱 폴더 밖에 캐시가 생기지 않음)
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --prefix .local/browser-tools --no-audit --no-fund playwright
node scripts/collection_smoke.cjs && node scripts/grouping_smoke.cjs && node scripts/obsidian_smoke.cjs && node scripts/search_smoke.cjs
```
