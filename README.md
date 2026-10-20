# Kanban

Claude Code와 Codex의 대화형 세션을 자동으로 모으는 macOS용 로컬 칸반 앱입니다. Python 3.9 이상이 필요하며, 추가 Python 패키지는 설치하지 않습니다.

## 목차

- [빠른 시작](#빠른-시작)
- [자동 수집](#자동-수집)
- [검색](#검색)
- [화면 기능](#화면-기능)
- [Obsidian 연동](#obsidian-연동)
- [명령어와 설정](#명령어와-설정)
- [데이터 관리](#데이터-관리)
- [문제 해결](#문제-해결)
- [저장과 독립성](#저장과-독립성)
- [검증](#검증)

## 빠른 시작

실행 후 Codex나 Claude Code에서 작업하면 프로젝트와 카드가 자동으로 생깁니다.

### 준비물

| 항목 | 필요 여부 | 확인 / 설치 |
| --- | --- | --- |
| macOS | 필수 | 12 이상 권장 (macOS 26에서 검증) |
| Python 3.9 이상 | 필수 | `python3 --version` · 없으면 `xcode-select --install` (권장, 시스템 Python 3.9 설치) 또는 `brew install python` |
| Codex 또는 Claude Code | 둘 중 하나 이상 | 평소처럼 한 번 이상 사용해 세션 로그가 있어야 카드가 생깁니다 |
| Git | 받는 방법에 따라 | `git --version` (Xcode Command Line Tools에 포함) |
| 추가 패키지 | 없음 | `pip install` 불필요 (표준 라이브러리만 사용) |

`codex`·`claude` 명령 자체는 세션 수집에 필요 없습니다. 해당 도구의 세션 로그만 읽습니다.

### 받기

**Git으로 받기 (권장)**

```bash
mkdir -p ~/Projects       # 원하는 위치
cd ~/Projects
git clone https://github.com/LeeYuHoon/Kanban-board.git
cd Kanban-board
```

**폴더로 받기**: 다른 Mac에서 복사할 때 `.local/`은 제외하세요. 데이터까지 옮기려면 [백업과 데이터 이동](#백업과-데이터-이동)을 보세요.

**zip으로 받기**: `Kanban.command`가 열리지 않을 때만 격리 속성을 해제합니다.

```bash
xattr -dr com.apple.quarantine ~/Projects/Kanban-board
```

### 실행

1. 터미널에서 `Kanban-board/bin`으로 이동합니다. Git 받기 단계를 마쳤다면:

   ```bash
   cd bin
   ```

   새 터미널이라면 설치 위치를 입력합니다. `~/Projects`에 받았다면:

   ```bash
   cd ~/Projects/Kanban-board/bin
   ```

   다른 위치라면 경로를 바꾸세요. Finder의 `bin` 폴더를 터미널의 `cd ` 뒤에 끌어다 놓아도 됩니다.

2. 아래 명령을 순서대로 실행합니다. `./`는 생략하지 마세요.

   ```bash
   ./kanban doctor     # 환경 점검
   ./kanban start      # 백그라운드 실행
   ./kanban open       # 브라우저 열기 (처음에는 http://127.0.0.1:31229)
   ```

3. 최근 7일의 세션이 카드로 등록됩니다. 새 기록은 약 5초 안에 반영됩니다.

Finder에서는 `Kanban.command`를 더블클릭해 실행할 수도 있습니다. macOS가 막으면 우클릭 → **열기**를 누르세요.

## 자동 수집

- 서버가 약 5초마다 로컬 세션 로그를 읽고, UI는 3초마다 갱신합니다.
- 첫 실행에는 **최근 7일 내 수정된 세션 파일**을 수집합니다. 이미 수집한 카드는 이후에도 남습니다.
- 작업 폴더별로 프로젝트를 등록하고 **세션당 카드 하나**를 만듭니다.
- 같은 대화를 이어가면 카드 요약은 최신 요청으로 갱신하지만, **이전 요청·답변은 대화 이력에 각각 보존**합니다.
- 카드 상세의 **대화 이력**에서 질문을 펼쳐 당시 답변을 확인합니다. 30개씩 표시하며 **이전 대화 더 보기**로 계속 조회합니다.
- 이력 기능 도입 전 수집한 로그도 최초 재실행 시 다시 읽어 이전 대화를 복구합니다. 원본 로그가 남아 있는 수집 대상에 한합니다.
- 완료는 최신 응답이 끝났다는 뜻입니다. 다음 요청이 들어오면 다시 진행 중이 됩니다.
- 실제 완료 이벤트가 있어야 완료로 표시합니다. 진행 중 기록이 15분 이상 갱신되지 않으면
  **상태 확인**으로 표시하고, 강제 종료인지 장시간 작업인지 추측하지 않습니다.
- 도구 출력, 내부 추론, 하위 에이전트 세션은 카드로 등록하지 않습니다.
- 보관한 세션은 후속 대화가 있어도 보관 상태를 유지합니다.
- 새 로그 부분만 읽으며 읽기 위치를 DB에 저장합니다. 재시작·반복 수집 시 중복 등록하지 않습니다.
- 원본 로그, CLI 설정, 기존 hooks를 변경하지 않습니다.

기본 탐색 위치:

- Codex: `~/.codex/sessions`, `~/.codex/archived_sessions`, `CODEX_HOME`의 동일 하위 폴더
- macOS Orca Codex 계정: `~/Library/Application Support/orca/codex-accounts/*/home/sessions`
  및 `archived_sessions`
- Claude Code: `~/.claude/projects`, `CLAUDE_CONFIG_DIR/projects`

터미널·IDE·Orca 중 **이 컴퓨터의 위 위치에 네이티브 세션 로그를 남기는 실행**을 수집합니다.
로컬 로그가 없는 원격·클라우드 작업은 지원하지 않습니다.
로그 형식은 도구 버전에 따라 바뀔 수 있으며 알 수 없는 레코드는 실행하지 않고 건너뜁니다.

화면의 **지금 확인**, **일시정지 / 수집 재개**로 수집을 관리합니다.
일시정지는 서버를 재시작해도 유지됩니다. 재개하면 최근 7일 범위의 누락분을 읽습니다.
서버가 꺼져 있을 때는 수집하지 않으며, 다음 실행에서 같은 범위 내 기록을 따라잡습니다.
브라우저 탭만 닫아도 서버가 실행 중이면 수집은 계속됩니다.

사용량은 Codex의 보고된 세션 누적값 또는 Claude Code의 최근 모델 응답값입니다.
상세 화면에 범위를 표시하며, Claude 사용량을 전체 세션 합계로 추정하지 않습니다.
요청·응답 텍스트는 각각 최대 100,000자까지 저장합니다.

### 별도 로그 위치 / 테스트

`KANBAN_SESSION_ROOTS`를 지정하면 기본 위치 대신 지정한 위치만 읽습니다.
경로는 Codex의 `sessions` 디렉터리, Claude의 `projects` 디렉터리를 가리켜야 합니다.

```bash
KANBAN_SESSION_ROOTS='{"codex":["/custom/codex/sessions"],"claude":["/custom/claude/projects"]}' python3 -m kanban serve
python3 -m kanban collect  # 현재 설정으로 한 번 수집 (대형 파일은 반복 실행 필요)
python3 -m kanban serve --no-collect  # 이 서버에서 수집 비활성화
```

## 검색

보드 위의 검색 패널에서 **모든 프롬프트의 요청·답변**을 검색어나 기간으로 찾습니다(카드의 최신 요청만이 아니라 전체 이력).

- 검색어: 띄어쓰기로 나눈 단어가 모두 들어간 프롬프트를 찾습니다(부분 문자열, 대소문자 무시). 에이전트가 자동 실행한 하위 세션도 검색되어 상위 카드로 표시됩니다.
- 검색 대상: 요청 + 답변 / 요청만 / 답변만. 범위: 현재 프로젝트 / 전체 프로젝트(프로젝트를 골랐을 때).
- 기간(프롬프트 시작 시각 기준): 전체 · 오늘 · 최근 7일 · 최근 30일 · 직접 지정(시작일~종료일). 검색어 없이 기간만 고르면 그 기간에 대화한 카드만 남습니다.
- **카드로 보기**: 일치한 카드만 남기고 일치 프롬프트 수와 첫 일치 문장을 하이라이트로 보여 줍니다.
  **프롬프트로 보기**: 일치한 프롬프트를 날짜별 목록으로 보여 주며(최근 300개) ‘카드 열기’로 해당 프롬프트가 펼쳐진 상세로 이동합니다.
- 검색 중 카드를 열면 일치한 프롬프트만 펼치고 하이라이트합니다. ‘일치만 보기’를 끄면 전체 이력이 보입니다.
- 입력 0.3초 뒤 자동 검색, `/`로 검색칸 이동, Esc로 지우기, ‘초기화’로 모든 조건 해제. 기존 도구 필터·보관함과 함께 적용됩니다.
- 검색은 서버에서 SQLite로 수행하며(`GET /api/search`) 모델을 호출하지 않습니다.

## 화면 기능

프로젝트별 보기, 도구 필터, 전체 이력 검색(아래 ‘검색’), 보관·해제,
열린 상세 화면의 결과 자동 갱신, 응답 복사, 모바일 레이아웃을 지원합니다.
상세창·안내창은 바깥 배경을 클릭하거나 Escape로 닫을 수 있습니다.
상세창의 **프롬프트 기록**에서 요청 한 건마다 요청 → 답변 → 스킬·MCP·에이전트를 한 묶음으로 표시합니다.
각 묶음에 세션 ID, 요청 ID, 요청 시각과 상태를 표시하며 별도로 펼칠 필요가 없습니다.
모델과 일반 도구도 같은 묶음 안에서 확인할 수 있습니다.
MCP는 서버 / 도구 이름으로 구분합니다.
로그에 명시된 항목만 수집하며 원시 도구 인자나 추론 내용은 저장하지 않습니다.
`SKILL.md` 경로나 `orca skills get` 명령의 참조는 **스킬 파일 참조**로 구분하며 실행 확정으로 취급하지 않습니다.

수집 기준:

- **Codex**: 최근 Codex는 도구를 `exec` 스크립트(`tools.apply_patch(...)` 등) 안에서 호출합니다. 스크립트 안의 도구 이름을
  각각 일반 도구로 기록합니다(`clock.*`, `web.*`, `image_gen.*` 같은 Codex 내장 도구 포함, 인자는 저장하지 않음).
  하위 에이전트는 `spawn_agent`뿐 아니라 실행 중인 에이전트에게 보낸 `send_message`·`followup_task`와 `SubAgentActivity`
  기록으로도 그 프롬프트에 표시합니다. Codex에는 스킬 호출 도구가 없어 `SKILL.md`를 읽은 기록만 **참조**로 표시하며,
  `AGENTS.md`의 사용 가능 스킬 목록은 사용으로 보지 않습니다.
- **Claude Code**: `Skill`, `Agent`(subagent_type), `mcp__서버__도구` 호출과 `SendMessage` 수신자(실행 중인 팀 에이전트)를 기록합니다.
- 중단 직후의 프롬프트처럼 실제로 아무 도구도 쓰지 않은 요청은 ‘확인된 기록 없음’이 맞는 결과입니다.
- **설명 보기**: 스킬·MCP·에이전트 칩에 마우스를 올리거나(키보드 포커스·모바일 탭도 가능) 하면 설명과 출처가 뜹니다.
  설명은 추측하지 않고 다음에서만 가져옵니다: Claude Code가 받은 스킬·에이전트 목록과 MCP 도구 정의(세션 로그),
  설치된 `SKILL.md`의 `description`. MCP 서버 문구는 에이전트에게 전달된 안내문이라 그렇게 표시합니다.
  `inq-fe`처럼 이름 붙인 에이전트는 실행할 때의 유형(`general-purpose` 등)의 설명을 보여 주고,
  Claude가 내부 ID로 메시지를 보낸 에이전트는 실행 기록으로 이름을 찾아 표시합니다.
  Codex 하위 에이전트의 역할 내용은 로그에 암호화되어 있어 그 사실만 알립니다. 설명을 못 찾으면 ‘설명을 찾지 못했습니다’라고 표시합니다.
- **내 설명**: 스킬·MCP·에이전트·일반 도구 칩을 클릭하면(키보드는 Enter/Space) 설명 창이 고정되고
  **설명 작성 / 내 설명 수정**으로 직접 적을 수 있습니다. 설명이 없으면 ‘설명이 없습니다. 클릭해서 작성해 주세요.’라고 안내합니다.
  빈칸으로 저장하면 삭제됩니다. 내 설명은 로그·SKILL.md에서 가져온 기록과 따로 저장되어(최대 2,000자) 둘 다 출처와 함께 보이며,
  재시작해도 유지됩니다. Kanban은 설명을 만들기 위해 모델을 호출하지 않습니다.
- **외부 검색 링크**: 설명 창의 링크로 공개 디렉터리에서 같은 이름을 찾아볼 수 있습니다(새 탭, referrer 미전송).
  스킬·에이전트·MCP는 [AugmentClaude](https://augmentclaude.com/)(`/search?q=`), 스킬과 MCP는 [Smithery](https://smithery.ai/)
  (스킬 `/skills?q=`, MCP `/search?q=`)도 함께 제공합니다. Smithery에는 에이전트 목록이 없습니다.
  `plugin:name`은 `name`으로 검색합니다. 프로젝트 전용 스킬(프로젝트의 `.claude/skills` 등), 이름 붙인 팀 에이전트,
  Codex 하위 에이전트, 내부 ID, Claude Code 기본 에이전트 유형은 외부로 보내지 않아 링크가 없습니다.
  외부 사이트의 목록은 제3자 정보이므로 설치 전 직접 확인하세요.
- 수집 규칙이 바뀌면 이미 수집한 로그를 한 번 다시 읽어 과거 프롬프트의 사용 내역도 보충합니다(7일보다 오래된 파일 포함,
  큰 파일은 여러 번에 나눠 끝까지 읽음).
기존 수집 대상 로그도 재해석해 요청별 기록을 보충합니다.
자동 수집 카드는 원래 세션에서 계속 작업하세요. 원본 실행을 수정하거나 중단하지 않습니다.

웹 화면과 CLI에서는 작업을 직접 만들거나 실행하지 않습니다. 카드는 Codex·Claude Code 세션에서 자동으로 생기며,
보드는 진행 중 · 완료 · 확인 필요 세 칸입니다.

상세창의 프롬프트 기록은 **기본으로 접혀** 있으며 프롬프트를 눌러 펼치거나 **모두 펼치기 / 모두 접기**를 사용합니다.
펼친 상태는 실시간 갱신 중에도 유지됩니다.

## Obsidian 연동

Kanban의 대화를 Obsidian에서 읽고 검색할 수 있도록 **Kanban → Obsidian 방향으로만** Markdown 노트를 내보냅니다.
Obsidian 플러그인·MCP 서버·유료 서비스가 필요 없고, Obsidian의 노트 편집은 Kanban으로 돌아오지 않습니다.

### 프로젝트별 연결과 저장 방식

Obsidian 연동은 **프로젝트마다 따로** 설정합니다. 왼쪽 메뉴 하단의 OBSIDIAN 블록에서 **연동 설정**을 누르면
보이는 프로젝트마다 저장 방식과 Vault 경로를 정할 수 있습니다.

| 저장 방식 | 동작 |
| --- | --- |
| 연동 안 함 | 이 프로젝트는 저장하지 않습니다(기본값). 끄더라도 이미 만든 노트는 지우지 않습니다. |
| 자동 저장 | 이 프로젝트의 **모든 프롬프트**를 자동으로 저장합니다. 15초마다 바뀐 세션만 갱신합니다. |
| 선택 저장 | 카드 상세의 각 프롬프트에 있는 **☆ Obsidian에 저장**을 누른 프롬프트만 저장합니다. 누르는 즉시 저장되며, 그 프롬프트의 답변이 나중에 완료돼도 갱신됩니다. 그 프롬프트가 실행한 에이전트 하위 세션도 함께 저장됩니다. |

1. **연동 설정**에서 프로젝트의 저장 방식을 고르고, Obsidian Vault(`.obsidian` 폴더가 있는 폴더)의 **절대 경로**를 입력해 **저장**합니다.
   저장 시 경로 존재, 폴더 여부, `.obsidian` 유무, 쓰기 권한, `Kanban` 항목의 심볼릭 링크 여부를 확인합니다.
   마지막으로 쓴 Vault가 다음 프로젝트 입력란에 미리 채워집니다. 프로젝트마다 다른 Vault를 써도 됩니다.
2. 사이드바에서 프로젝트를 고르면 OBSIDIAN 블록이 그 프로젝트의 저장 방식·마지막 성공·오류를 짧게 보여 줍니다
   (전체 내용은 마우스를 올리거나 연동 설정 창에서 확인). 모바일에서는 상단 메뉴 아래 한 줄로 표시됩니다.
   **지금 동기화**는 연결된 모든 프로젝트를 바로 다시 저장합니다(오류 후 재시도용).

선택 저장 노트에는 고른 프롬프트만 원래 번호(`## 프롬프트 7 · …`)로 들어가며 속성에
`kanban_mode: "selected"`, `turn_count`(저장한 수), `session_turn_count`(세션 전체 수)가 붙습니다.
선택을 모두 해제하면 노트 갱신만 멈추고 노트는 지우지 않습니다.
Kanban은 Vault 경로를 추측하지 않으며, 프로젝트를 연결하기 전에는 어떤 파일도 쓰지 않습니다.
설정·선택·프로젝트별 마지막 결과는 DB에 저장되어 재시작해도 유지됩니다.

### 노트 구조

```text
<Vault>/
└── Kanban/                         # Kanban은 이 폴더 안에만 씁니다
    ├── .kanban-sync.lock           # 동시 동기화 방지용 숨김 파일
    └── <프로젝트 이름>/              # 같은 이름의 다른 폴더는 " (해시6자리)"로 구분
        ├── 2026-10-14 로그인 버그 고쳐줘 (Claude 7c7e3482).md
        ├── 2026-10-14 API 구조 설명 (Codex 019a2b3c).md
        └── … (Kanban 최신본).md      # 편집 충돌 시에만 생성
```

- 세션 하나당 노트 하나. 파일명은 `생성일 첫 요청 (도구 세션ID앞8자리)`이며 한 번 정한 경로는 바뀌지 않습니다.
- 노트 상단 속성: `kanban_id`(provider:session_id), `title`, `project`, `project_path`, `provider`, `provider_label`,
  `session_id`, `status`, `archived`, `turn_count`, `created`, `updated`, `skills`, `mcp_servers`, `agents`, `models`, `tags`.
- 본문은 오래된 프롬프트부터 `## 프롬프트 N · 시각 · 상태` 묶음으로 세션 ID·요청 ID·시각·상태 → 요청 → 답변 →
  스킬 / 스킬 파일 참조 / MCP 서버·도구 / 실행·하위 에이전트 / 모델 / 일반 도구를 기록합니다.
- 화면의 30개 페이지와 무관하게 **세션의 전체 이력**을 내보냅니다.
- 요청·답변은 내용보다 긴 펜스의 코드 블록에 원문 그대로 넣어 Markdown 특수문자, 코드 블록, 여러 줄, 한글을 보존합니다.
  속성 값은 이스케이프된 따옴표 문자열입니다.
- 기록이 없는 항목은 `확인된 기록 없음`, 답변이 없으면 `기록된 최종 답변이 없습니다`로 표시하며 추측하지 않습니다.
- 원시 도구 인자와 추론 내용은 Kanban이 처음부터 저장하지 않으므로 노트에도 없습니다. 인증 정보를 새로 내보내지 않습니다.
  단, 사용자가 프롬프트에 직접 쓴 내용은 화면과 같이 그대로 내보냅니다.
- 에이전트 자동 실행 세션은 별도 노트가 아니라 상위 노트의 해당 프롬프트 아래 `### 에이전트가 실행한 하위 세션 (N)`에
  요청·답변·사용 내역 전체가 들어갑니다. Obsidian에서는 제목 옆 화살표로 각 프롬프트·하위 세션을 접을 수 있습니다.
- 묶기 이전에 따로 만들어진 노트는 **미편집인 경우에만** 숨김 폴더 `Kanban/.kanban-merged/`로 옮깁니다(삭제하지 않음,
  Obsidian 목록에는 보이지 않음). 편집된 노트는 그 자리에 둡니다. 옮긴 뒤 완전히 빈 Kanban 프로젝트 폴더만 제거합니다.

카드 상세의 **Obsidian에서 열기**는 `obsidian://open?path=…` 링크로 해당 노트를 엽니다.
Vault가 Obsidian에 등록되어 있어야 하며, Obsidian이 설치된 같은 컴퓨터에서 동작합니다.

Vault가 `문서`·`데스크탑`·iCloud Drive 안에 있으면 macOS가 접근 권한을 물을 수 있습니다.
자동 실행 중 노트가 저장되지 않으면 시스템 설정의 **개인정보 보호 및 보안 → 파일 및 폴더**에서
`./kanban doctor`에 표시된 Python의 접근 권한을 확인하세요.

## 명령어와 설정

### 자주 쓰는 명령

모든 `./kanban` 명령은 `Kanban-board/bin`에서 실행합니다. 기본 포트는 `31229`이며, 변경한 포트는 저장됩니다.

| 명령 | 설명 |
| --- | --- |
| `./kanban start` | 저장된 포트로 백그라운드 실행 (처음에는 31229) |
| `./kanban stop` | 종료 |
| `./kanban restart` | 다시 시작 (코드 업데이트 후) |
| `./kanban status` | 실행 여부, 카드 수, 수집 상태 |
| `./kanban open` | 브라우저로 열기 |
| `./kanban logs` | 서버 로그 보기 (Ctrl+C로 나가기) |
| `./kanban backup [파일]` | DB 백업 (실행 중에도 가능) |
| `./kanban doctor` | 환경 점검 |
| `./kanban foreground` | 현재 터미널에서 실행 (Ctrl+C로 종료) |
| `./kanban install-login` | Mac 사용자 로그인 시 자동 실행 |
| `./kanban uninstall-login` | 자동 실행 해제 |

포트 지정 명령은 [포트 변경](#포트-변경)을 보세요.

### Mac 사용자 로그인 시 자동 실행

Mac을 켜고 **사용자 계정에 로그인할 때** 자동 실행하려면 한 번 등록하세요.

```bash
./kanban install-login
```

| 상황 | 명령·동작 |
| --- | --- |
| 자동 실행 포트 | 저장된 포트를 사용. 변경하려면 등록 전에 [포트 변경](#포트-변경) |
| 이번 로그인에서만 중지 | `./kanban stop` |
| 자동 실행 해제 | `./kanban uninstall-login` |
| 앱 폴더 또는 Python 위치 변경 | `./kanban install-login` 재실행 |

등록 후 비정상 종료되면 macOS가 다시 실행합니다. 사용자 로그인 전에는 실행되지 않습니다.

### 포트 및 기타 설정

기본 포트는 **31229**입니다. 다른 포트를 쓰려면 아래 숫자를 빈 포트 번호로 바꾸세요.

#### 포트 변경

| 할 일 | 명령 | 결과 |
| --- | --- | --- |
| 포트 지정해 실행 | `./kanban start KANBAN_PORT='31229'` | 실행 중인 Kanban 서버를 종료하고 31229에서 하나만 시작·저장 |
| 포트 지정해 재시작 | `./kanban restart KANBAN_PORT='31229'` | 실행 중인 Kanban 서버를 종료한 뒤 현재 실행 방식으로 31229에서 다시 시작·저장 |

| 규칙 | 동작 |
| --- | --- |
| 지정 가능 명령 | `start`, `restart`만 가능 |
| 저장 | 시작 성공 시 `.local/port`에 저장. `open`·`status`·`stop`·`doctor`와 `Kanban.command`에도 적용 |
| 기존 Kanban 서버 | 같은 Mac 사용자에게 실행 중인 서버를 종료하고 하나만 실행 |
| 포트 충돌 | 사용 중인 포트와 PID를 표시하고 중단. 기존 서버·저장 포트 유지 |

#### 기타 설정

아래 환경 변수는 명령 **앞에** 붙입니다. 자동 실행에도 적용하려면 `install-login`에 붙이세요.

| 설정 | 명령 예시 | 용도 |
| --- | --- | --- |
| Python 지정 | `KANBAN_PYTHON=/opt/homebrew/bin/python3 ./kanban start` | 사용할 Python 선택 |
| DB 위치 변경 | `KANBAN_DB=~/kanban-data/kanban.sqlite3 ./kanban start` | 데이터를 저장할 파일 변경 |
| 세션 로그 위치 변경 | `KANBAN_SESSION_ROOTS='{"codex":["/path/codex/sessions"],"claude":["/path/claude/projects"]}' ./kanban start` | 기본 탐색 위치 대신 지정한 폴더만 읽기 |

## 데이터 관리

### 백업과 데이터 이동

백업 대상은 `.local/kanban.sqlite3`입니다. Obsidian 설정·내 설명·선택 저장도 포함됩니다.

1. 기존 Mac에서 `./kanban backup ~/Desktop/kanban.sqlite3` (실행 중에도 일관된 파일 하나로 저장됩니다)
2. 새 Mac에서 `./kanban stop` 후 그 파일을 프로젝트 폴더의 `.local/kanban.sqlite3`로 복사(`bin` 폴더 기준으로는 `../.local/kanban.sqlite3`)
3. 새 Mac에서 `./kanban start`

새 Mac에서는 그 Mac의 세션만 계속 수집합니다. 백업 없이 새로 시작해도 됩니다.

### 업데이트

`bin` 폴더에서 실행합니다.

```bash
git pull
./kanban restart
```

DB 구조 변경은 시작할 때 자동 적용되며 기존 데이터는 유지됩니다.

### 완전 제거

```bash
./kanban uninstall
```

삭제 항목을 확인한 뒤 **`삭제` 또는 `delete`**를 입력해야 진행합니다(스크립트에서는 `--yes`).

- 실행 중인 서버와 로그인 자동 실행(`~/Library/LaunchAgents/com.kanban.server.plist`, 그 폴더를 Kanban이 만들었다면 폴더도)
- DB 전체: 앱 폴더의 `.local/`(DB·로그·백업·PID) — `KANBAN_DB`로 다른 곳에 두었다면 그 DB 파일과 `-wal`·`-shm`(빈 폴더 포함).
  자동 실행 설정에 저장된 `KANBAN_DB`도 찾아서 지웁니다
- 연결했던 Obsidian Vault의 `Kanban/` 폴더(숨김 폴더·편집본 포함). 노트를 남기려면 `./kanban uninstall --keep-obsidian`
- 앱 폴더 전체(`Kanban-board/`)와, 시스템 Python이 이 앱 경로에 남긴 캐시

다음은 남습니다.

- Codex·Claude Code의 세션 로그와 설정(원래 그 도구들의 파일)
- Obsidian Vault의 다른 노트와 `.obsidian` 설정
- 브라우저에 저장된 화면 설정(선택한 프로젝트·검색 보기 방식): 브라우저 설정에서 `127.0.0.1` 사이트 데이터를 삭제하면 지워집니다

DB를 남기고 싶다면 제거 전에 `./kanban backup ~/Desktop/kanban.sqlite3`로 백업하세요.

## 문제 해결

| 증상 | 해결 |
| --- | --- |
| `Python 3.9 이상이 필요합니다` | `xcode-select --install` 후 다시 실행. Homebrew Python은 `KANBAN_PYTHON`으로 지정 |
| `포트 …를 다른 프로그램이 쓰고 있습니다` | 비어 있는 포트를 골라 `./kanban start KANBAN_PORT='포트번호'` 또는 `./kanban restart KANBAN_PORT='포트번호'`로 변경 |
| 카드가 하나도 없음 | `./kanban doctor`로 세션 로그 폴더 확인. 최근 7일 안에 Codex·Claude Code를 쓴 기록이 있어야 함. 로그가 다른 곳이면 `KANBAN_SESSION_ROOTS` |
| 브라우저에 `Local access only` | 주소를 `http://127.0.0.1:저장된포트` 또는 `http://localhost:저장된포트`로 직접 입력 (처음에는 31229) |
| 시작이 안 됨 | `./kanban logs`, `./kanban foreground`로 오류 확인 |
| 원격·클라우드 Codex/Claude 작업이 안 보임 | 이 Mac에 세션 로그가 남는 실행만 수집합니다 |

## 저장과 독립성

| 항목 | 동작 |
| --- | --- |
| 저장 위치 | `.local/kanban.sqlite3` (Git 제외). `KANBAN_DB`로 변경 가능 |
| DB 업데이트 | 시작 시 구조를 자동 변경하고 기존 카드 유지 |
| 접근 범위 | `127.0.0.1`에서만 접속. Host/Origin 검증과 JSON 요청 제한 적용 |
| 데이터 | 요청과 결과를 이 컴퓨터의 DB에 저장. 로컬 단일 사용자용 |
| 백업 | [`./kanban backup`](#백업과-데이터-이동) 사용 |

## 검증

```bash
python3 -m unittest discover -s tests -v
node --check web/app.js
# 브라우저 자동 수집 검증 (별도 임시 DB·로그·Chrome 프로필 사용)
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --prefix .local/browser-tools --no-audit --no-fund playwright
node scripts/collection_smoke.cjs
node scripts/obsidian_smoke.cjs  # 임시 Vault로 설정·동기화·자동 동기화·오류·충돌 UI 검증
node scripts/grouping_smoke.cjs  # 자동 실행 세션 묶기·프롬프트 접기 검증
node scripts/search_smoke.cjs    # 검색어·기간 검색, 카드/프롬프트 보기
```

브라우저 테스트는 macOS Chrome을 사용합니다. `KANBAN_CHROME`으로 실행 파일 경로를 바꿀 수 있습니다.
실제 모델 호출·과금 없이 로그 추가만으로 자동 등록·완료·재개·일시정지·중복 방지를 검증합니다.
[검증 기록](docs/VERIFICATION.md)을 참고하세요.
