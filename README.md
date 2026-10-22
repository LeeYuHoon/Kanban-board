# Kanban

Claude Code와 Codex의 대화형 세션을 자동으로 모으는 macOS용 로컬 칸반 앱. Python 3.9 이상이 필요하고 추가 패키지는 필요 없다.

## 목차

1. [빠른 시작](#1-빠른-시작-맨위로)
2. [자동 수집](#2-자동-수집-맨위로)
3. [검색](#3-검색-맨위로)
4. [화면 기능](#4-화면-기능-맨위로)
5. [Obsidian 연동](#5-obsidian-연동-맨위로)
6. [명령어와 설정](#6-명령어와-설정-맨위로)
7. [업데이트](#7-업데이트-맨위로)
8. [데이터 관리](#8-데이터-관리-맨위로)
9. [제거](#9-제거-맨위로)
10. [문제 해결](#10-문제-해결-맨위로)

# 1. 빠른 시작 <sup><a href="#목차">맨위로</a></sup>

실행 후 Codex나 Claude Code에서 작업하면 프로젝트와 카드가 자동으로 생긴다.

## 준비물

| 항목 | 필요 여부 | 확인 / 설치 |
| --- | --- | --- |
| macOS | 필수 | 12 이상 권장 (macOS 26에서 검증) |
| Python 3.9 이상 | 필수 | `python3 --version` · 없으면 [Python 공식 macOS 설치 파일](https://www.python.org/downloads/macos/)로 설치 |
| Codex 또는 Claude Code | 둘 중 하나 이상 | 세션 로그가 있어야 카드 생성 |
| Git | 필수 | `git --version` |
| 추가 패키지 | 없음 | `pip install` 불필요 (표준 라이브러리만 사용) |

수집에는 `codex`·`claude` 명령이 필요 없다. 세션 로그만 읽는다.

## 설치와 실행

터미널에서 순서대로 실행한다.

```bash
mkdir -p ~/Projects       # 원하는 설치 위치
cd ~/Projects
git clone https://github.com/LeeYuHoon/Kanban-board.git
cd Kanban-board/bin
./kanban doctor           # 환경 점검
./kanban start            # 백그라운드 실행
./kanban open             # 브라우저 열기
```

새 터미널에서는 먼저 `cd ~/Projects/Kanban-board/bin`을 실행한다. 설치 위치가 다르면 경로를 바꾼다. `./`는 현재 폴더의 `kanban`을 실행한다.

기본 주소: `http://127.0.0.1:31229`. 기존 로그 전체를 확인하고 새 기록을 수집한다.

# 2. 자동 수집 <sup><a href="#목차">맨위로</a></sup>

| 상황 | 수집 방법 |
| --- | --- |
| 첫 실행·서버 재시작 | 날짜 제한 없이 로컬 세션 로그 전체를 대조한다. |
| 실행 중·잠자기 복귀 | 약 5초마다 파일 크기·수정 시간을 확인한다. 바뀐 파일만 열어 새 기록을 읽는다. |
| 큰 로그 | 보통 한 번에 8MB씩 읽고 이어 읽는다. 한 행이 8MB를 넘으면 그 행을 끝까지 읽는다. |
| 읽기 오류 | 잘못된 JSON 행은 원문과 위치를 로컬 DB에 보존한다. |
| 일시정지 후 재개 | 저장한 읽기 위치부터 누락분을 수집한다. |
| 화면 | 보이는 탭만 3초마다 확인한다. 변경이 없으면 카드 데이터를 다시 보내지 않는다. 탭을 다시 열면 즉시 확인한다. |

작업 폴더별로 프로젝트를, 세션별로 카드 하나를 만든다. 이어진 대화는 최신 요청으로 갱신하고 이전 요청·답변은 **대화 이력**에 남긴다(한 번에 30개씩 표시). 완료 기록 없이 15분간 새 기록이 없으면 **상태 확인**으로 표시한다. 보관한 카드는 후속 대화가 생겨도 보관 상태를 유지한다.

도구 출력·내부 추론·하위 에이전트 세션은 별도 카드로 만들지 않는다. 원본 로그·CLI 설정·hooks는 바꾸지 않는다.

기본 탐색 위치:

- Codex: `~/.codex/sessions`, `~/.codex/archived_sessions`, `CODEX_HOME`의 동일 하위 폴더
- macOS Orca Codex 계정: `~/Library/Application Support/orca/codex-accounts/*/home/sessions`
  및 `archived_sessions`
- Claude Code(Orca 포함): `~/.claude/projects`, `CLAUDE_CONFIG_DIR/projects`, Orca의 `claude-profiles/*/home/projects`

터미널·IDE·Orca에서 실행해 위 위치에 로그가 남은 세션을 수집한다. 로컬 로그가 없는 원격 작업은 제외한다.

화면의 **지금 확인**, **일시정지 / 수집 재개**로 수집을 관리한다. 일시정지는 재시작 후에도 유지된다. 브라우저 탭을 닫아도 서버가 켜져 있으면 수집한다.

사용량은 Codex의 세션 누적값 또는 Claude Code의 최근 모델 응답값이다. 요청·응답은 각각 최대 100,000자까지 저장한다.

## 별도 로그 위치 / 테스트

`KANBAN_SESSION_ROOTS`를 지정하면 해당 위치만 읽는다. Codex의 `sessions`, Claude의 `projects` 폴더를 지정한다.

```bash
KANBAN_SESSION_ROOTS='{"codex":["/custom/codex/sessions"],"claude":["/custom/claude/projects"]}' python3 -m kanban serve
python3 -m kanban collect  # 현재 설정으로 한 번 수집 (대형 파일은 반복 실행 필요)
python3 -m kanban serve --no-collect  # 이 서버에서 수집 비활성화
```

# 3. 검색 <sup><a href="#목차">맨위로</a></sup>

보드 위 검색 패널에서 **전체 대화 이력**의 요청·답변을 찾는다.

| 항목 | 동작 |
| --- | --- |
| 검색어 | 띄어쓴 단어를 모두 포함한 프롬프트 검색(부분 문자열, 대소문자 무시). 하위 에이전트 기록은 상위 카드로 표시 |
| 대상·범위 | 요청+답변 / 요청만 / 답변만 · 현재 프로젝트 / 전체 프로젝트 |
| 기간 | 전체 / 오늘 / 최근 7일 / 최근 30일 / 직접 지정. 검색어 없이 기간만 지정 가능 |
| 카드로 보기 | 일치한 카드와 프롬프트 수·첫 일치 문장 표시 |
| 프롬프트로 보기 | 최근 300개를 날짜별로 표시. ‘카드 열기’로 상세 이동 |

입력 0.3초 뒤 검색한다. `/`로 검색칸 이동, Esc로 지우기, ‘초기화’로 조건 해제. 검색은 로컬 SQLite에서 수행하며 모델을 호출하지 않는다.

# 4. 화면 기능 <sup><a href="#목차">맨위로</a></sup>

| 기능 | 동작 |
| --- | --- |
| 보드 | 프로젝트·도구별 보기, 보관·해제, 진행 중·완료·확인 필요 상태 |
| 카드 상세 | 요청 → 답변 → 사용한 스킬·MCP·에이전트·도구를 프롬프트별로 표시 |
| 실시간 갱신 | 열린 상세 화면의 결과도 자동 갱신. 프롬프트 펼침 상태 유지 |
| 편의 기능 | 응답 복사, 모바일 화면, 배경 클릭·Esc로 창 닫기 |

프롬프트 기록은 기본으로 접힌다. 개별 프롬프트나 **모두 펼치기 / 모두 접기**로 연다.
도구 사용 내역은 로그에 확인된 것만 표시한다. 원시 도구 인자와 내부 추론은 저장하지 않는다.
`SKILL.md` 경로 참조는 스킬 실행과 구분한다. 도구 사용 기록이 없으면 **확인된 기록 없음**으로 표시한다.

스킬·MCP·에이전트·도구 칩에 마우스를 올리면 설명과 출처가 나온다. 클릭하면 **내 설명**을 작성·수정할 수 있다. 내 설명은 별도로 저장되고 재시작 후에도 남는다. 설명을 만들기 위해 모델을 호출하지 않는다.

설명 창에서 [AugmentClaude](https://augmentclaude.com/)·[Smithery](https://smithery.ai/) 검색 링크를 제공한다. 내부 이름에는 링크를 표시하지 않는다.

카드는 Codex·Claude Code 세션에서 자동 생성된다. 작업은 원래 세션에서 이어간다.

# 5. Obsidian 연동 <sup><a href="#목차">맨위로</a></sup>

대화를 Markdown 노트로 내보낸다. **Kanban → Obsidian 단방향**이며 플러그인은 필요 없다. Obsidian에서 수정한 내용은 Kanban으로 돌아오지 않는다.

## 프로젝트별 연결과 저장 방식

왼쪽 메뉴 **OBSIDIAN → 연동 설정**에서 프로젝트별 Vault 경로와 저장 방식을 정한다.

| 저장 방식 | 동작 |
| --- | --- |
| 연동 안 함 | 저장 안 함(기본값). 기존 노트는 유지 |
| 자동 저장 | 모든 프롬프트 저장. 변경된 세션을 15초마다 갱신 |
| 선택 저장 | 카드 상세에서 **☆ Obsidian에 저장**을 누른 프롬프트만 저장. 이후 답변·하위 세션도 반영 |

1. 저장 방식을 고르고 Vault(`.obsidian` 폴더가 있는 폴더)의 **절대 경로**를 입력해 저장한다.
2. 사이드바에서 저장 방식·마지막 성공·오류를 확인한다. **지금 동기화**를 누르면 연결된 프로젝트를 즉시 다시 저장한다.

선택을 모두 해제해도 기존 노트는 남는다. 프로젝트를 연결하기 전에는 Vault에 쓰지 않는다. 설정은 재시작 후에도 유지된다.

## 노트 구조

```text
<Vault>/
└── Kanban/                         # Kanban이 쓰는 폴더
    ├── .kanban-sync.lock           # 동시 동기화 방지용 숨김 파일
    └── <프로젝트 이름>/              # 같은 이름의 다른 폴더는 " (해시6자리)"로 구분
        ├── 2026-10-14 로그인 버그 고쳐줘 (Claude 7c7e3482).md
        ├── 2026-10-14 API 구조 설명 (Codex 019a2b3c).md
        └── … (Kanban 최신본).md      # 편집 충돌 시에만 생성
```

- 세션당 노트 하나. 파일명은 `생성일 첫 요청 (도구 세션ID앞8자리)`이다.
- 프롬프트마다 요청·답변·사용 내역을 기록한다. 화면의 30개 단위와 관계없이 **전체 이력**을 내보낸다.
- 요청·답변은 원문 그대로 코드 블록에 넣는다. 원시 도구 인자와 내부 추론은 포함하지 않는다.
- 프롬프트에 직접 쓴 내용은 그대로 내보낸다.
- 기록이 없는 항목은 `확인된 기록 없음`, 답변이 없으면 `기록된 최종 답변이 없습니다`로 표시하며 추측하지 않는다.
- 하위 에이전트 기록은 해당 프롬프트 아래에 넣는다.
- 이전 형식의 노트는 **미편집인 경우에만** `Kanban/.kanban-merged/`로 옮긴다. 편집본은 그대로 둔다.

카드 상세의 **Obsidian에서 열기**는 같은 컴퓨터에 설치된 Obsidian에서 노트를 연다. Vault를 Obsidian에 등록해야 한다.

Vault가 `문서`·`데스크탑`·iCloud Drive 안에 있으면 macOS가 접근 권한을 물을 수 있다. 저장에 실패하면 **시스템 설정 → 개인정보 보호 및 보안 → 파일 및 폴더**에서 `./kanban doctor`에 표시된 Python의 권한을 확인한다.

# 6. 명령어와 설정 <sup><a href="#목차">맨위로</a></sup>

## 자주 쓰는 명령

모든 `./kanban` 명령은 `Kanban-board/bin`에서 실행한다. 기본 포트는 `31229`이며 변경한 포트는 저장된다.

| 명령 | 설명 |
| --- | --- |
| `./kanban start` | 저장된 포트로 백그라운드 실행 (처음에는 31229) |
| `./kanban stop` | 종료 |
| `./kanban restart` | 서버 재시작 |
| `./kanban update` | Git 업데이트 후 서버 재시작 (코드 변경 시 DB 백업) |
| `./kanban status` | 실행 여부, 카드 수, 수집 상태 |
| `./kanban open` | 브라우저로 열기 |
| `./kanban logs` | 서버 로그 보기 (Ctrl+C로 나가기) |
| `./kanban backup [파일]` | DB 백업 (실행 중에도 가능) |
| `./kanban doctor` | 환경 점검 |
| `./kanban foreground` | 현재 터미널에서 실행 (Ctrl+C로 종료) |
| `./kanban install-login` | Mac 사용자 로그인 시 자동 실행 |
| `./kanban uninstall-login` | 자동 실행 해제 |

포트 지정 명령은 [포트 변경](#포트-변경)에 있다.

## Mac 사용자 로그인 시 자동 실행

Mac **사용자 계정에 로그인할 때** 자동 실행하려면 한 번 등록한다.

```bash
./kanban install-login
```

| 상황 | 명령·동작 |
| --- | --- |
| 자동 실행 포트 | 저장된 포트를 사용. 변경하려면 등록 전에 [포트 변경](#포트-변경) |
| 이번 로그인에서만 중지 | `./kanban stop` |
| 자동 실행 해제 | `./kanban uninstall-login` |
| 앱 폴더 또는 Python 위치 변경 | `./kanban install-login` 재실행 |

비정상 종료되면 macOS가 다시 실행한다. 로그인 전에는 실행되지 않는다.

## 포트 및 기타 설정

기본 포트는 **31229**다. 다른 포트는 아래 명령의 숫자를 바꿔 지정한다.

### 포트 변경

| 할 일 | 명령 | 결과 |
| --- | --- | --- |
| 포트 지정해 실행 | `./kanban start KANBAN_PORT='31229'` | 실행 중인 Kanban 서버를 종료하고 31229에서 하나만 시작·저장 |
| 포트 지정해 재시작 | `./kanban restart KANBAN_PORT='31229'` | 실행 중인 Kanban 서버를 종료한 뒤 현재 실행 방식으로 31229에서 다시 시작·저장 |

| 규칙 | 동작 |
| --- | --- |
| 지정 가능 명령 | `start`, `restart`만 가능 |
| 저장 | 시작 성공 시 `.local/port`에 저장. `open`·`status`·`stop`·`doctor`에도 적용 |
| 기존 Kanban 서버 | 같은 Mac 사용자에게 실행 중인 서버를 종료하고 하나만 실행 |
| 포트 충돌 | 사용 중인 포트와 PID를 표시하고 중단. 기존 서버·저장 포트 유지 |

### 기타 설정

아래 환경 변수는 명령 **앞에** 붙인다. 로그인 자동 실행에도 적용하려면 `install-login`에 붙인다.

| 설정 | 명령 예시 | 용도 |
| --- | --- | --- |
| Python 지정 | `KANBAN_PYTHON=/opt/homebrew/bin/python3 ./kanban start` | 사용할 Python 선택 |
| DB 위치 변경 | `KANBAN_DB=~/kanban-data/kanban.sqlite3 ./kanban start` | 데이터를 저장할 파일 변경 |
| 세션 로그 위치 변경 | `KANBAN_SESSION_ROOTS='{"codex":["/path/codex/sessions"],"claude":["/path/claude/projects"]}' ./kanban start` | 기본 탐색 위치 대신 지정한 폴더만 읽기 |

# 7. 업데이트 <sup><a href="#목차">맨위로</a></sup>

`Kanban-board/bin`에서 실행한다.

```bash
./kanban update
```

1. **Git 확인** — 로컬 수정 여부를 확인하고 원격 변경사항을 가져온다.
2. **DB 백업** — 적용할 코드가 있으면 사용 중인 DB를 `.local/backups/`에 백업·검사한다.
3. **적용·재시작** — 코드를 반영하고 서버를 다시 시작한다. 필요한 DB 구조 변경도 적용한다.
4. **검증·정리** — 백업한 경우 DB 무결성과 카드 수를 확인한다. 성공 시 자동 백업은 최신 3개와 최근 구조 변경 직전 1개를 남긴다.

- **변경사항이 없어도** 서버는 재시작한다. 꺼져 있었다면 시작한다.
- **업데이트 전 오류**(로컬 수정·Git 충돌·네트워크·백업): 중단하고 실행 중인 서버를 유지한다.
- **재시작·DB 검증 오류**: 백업을 보존한다. 서버가 꺼져 있을 수 있다. `./kanban logs`로 확인한다.
- **수동 백업**은 자동 정리하지 않는다.

기존 설치에 `update`가 없다면 처음 한 번 `./kanban backup` → `git -C .. pull --ff-only` → `./kanban restart`를 실행한다.

# 8. 데이터 관리 <sup><a href="#목차">맨위로</a></sup>

## 백업과 데이터 이동

백업 대상은 `.local/kanban.sqlite3`이다. Obsidian 설정·내 설명·선택 저장도 포함한다.

1. 기존 Mac에서 `./kanban backup ~/Desktop/kanban.sqlite3` 실행(서버 실행 중에도 가능)
2. 새 Mac에서 `./kanban stop` 후 그 파일을 프로젝트 폴더의 `.local/kanban.sqlite3`로 복사(`bin` 폴더 기준으로는 `../.local/kanban.sqlite3`)
3. 새 Mac에서 `./kanban start`

새 Mac에서는 그 Mac의 세션만 계속 수집한다. 백업 없이 새로 시작할 수도 있다.

# 9. 제거 <sup><a href="#목차">맨위로</a></sup>

```bash
./kanban uninstall
```

| 선택 | 지우는 항목 | 남기는 항목 |
| --- | --- | --- |
| **1. 앱만 제거** | 서버·자동 실행·앱 폴더 전체·관련 Python 캐시 | DB·로그·백업은 홈 폴더의 `Kanban-data-*`에 보관. 외부 DB·Obsidian 노트 유지 |
| **2. 앱과 데이터 제거** | 서버·자동 실행·앱 폴더·관련 Python 캐시·DB·로그·백업·외부 DB | Obsidian 노트 유지 |
| **3. 모두 제거** | 2번 항목 + Obsidian Vault의 `Kanban/` 노트(직접 편집한 노트 포함) | Kanban 데이터 없음 |
| **0. 취소** | 없음 | 모두 유지 |

선택 후 실제 삭제 목록을 보여준다. **`삭제` 또는 `delete`**를 입력해야 진행한다. 앱 폴더의 다른 파일은 1번에서도 삭제한다.

스크립트용 옵션: `--yes`는 **3번**을 확인 없이 실행한다. `--yes --keep-obsidian`은 **2번**, `--yes --keep-data`는 **1번**이다.

어느 선택이든 Codex·Claude Code의 원본 로그와 Obsidian의 다른 노트는 남는다. 브라우저 화면 설정은 `127.0.0.1` 사이트 데이터를 직접 지워야 한다.

# 10. 문제 해결 <sup><a href="#목차">맨위로</a></sup>

| 증상 | 해결 |
| --- | --- |
| `Python 3.9 이상이 필요합니다` | [Python 공식 macOS 설치 파일](https://www.python.org/downloads/macos/)로 설치한 뒤 다시 실행 |
| `포트 …를 다른 프로그램이 쓰고 있습니다` | 비어 있는 포트를 골라 `./kanban start KANBAN_PORT='포트번호'` 또는 `./kanban restart KANBAN_PORT='포트번호'`로 변경 |
| 카드가 하나도 없음 | `./kanban doctor`로 세션 로그 폴더 확인. 로그가 다른 곳이면 `KANBAN_SESSION_ROOTS` 지정 |
| 브라우저에 `Local access only` | 주소를 `http://127.0.0.1:저장된포트` 또는 `http://localhost:저장된포트`로 직접 입력 (처음에는 31229) |
| 시작이 안 됨 | `./kanban logs`, `./kanban foreground`로 오류 확인 |
| 원격·클라우드 Codex/Claude 작업이 안 보임 | 이 Mac에 로그가 남는 세션만 수집 |
