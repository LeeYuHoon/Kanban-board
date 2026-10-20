# Kanban 검증 기록

## 완전 제거와 재설치 — 2026-10-15

- `bin/kanban uninstall`: 서버·로그인 자동 실행(LaunchAgent, Kanban이 만든 폴더 포함)·DB(앱 `.local/`, `KANBAN_DB` 외부 DB와 그 빈 폴더,
  자동 실행 설정에 저장된 `KANBAN_DB`까지)·연결된 Obsidian Vault의 `Kanban/`·앱 폴더·이 앱 경로의 시스템 Python 캐시 삭제.
  삭제 목록을 먼저 보여 주고 `삭제`/`delete` 입력 시에만 진행(`--yes`, `--keep-obsidian`). 앱 폴더는 임시 스크립트가 지우고 스스로 삭제.
  모든 삭제 경로에 `${VAR:?}` 보호.
- 격리 검증(가짜 HOME: 합성 Codex·Claude 로그, 사용자 노트가 있는 Vault, `~/Library` 없는 새 계정 / 별도 포트·launchd 라벨):
  설치·사용(Obsidian 노트·내 설명·백업·자동 실행) → 제거 → 외부 DB로 재설치 → 제거 → 확인 없는 제거 거부 → 제거.
  매번 **설치 전 파일·폴더 목록과 완전히 동일**(17개 항목), 사용자 파일 체크섬 동일, launchd 작업·포트·프로세스·/tmp 임시 파일 없음.
- `expect`로 실제 입력 확인: ‘아니요’ → 취소(아무것도 삭제 안 함), ‘삭제’(UTF-8) → 제거, ‘delete’(로캘 없음) → 제거.
- 발견·수정: 시스템 Python이 `~/Library/Caches/com.apple.python`에 바이트코드 캐시를 남김 → 실행기 전체에 `PYTHONDONTWRITEBYTECODE=1`
  (자동 실행 plist 포함). 첫 실행 시 로그 파일 없음 메시지. Playwright 설치가 `~/Library/Caches/ms-playwright`에 브라우저를 받던 안내
  → `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`(시스템 Chrome 사용).
- 이 Mac의 실제 앱 폴더는 커밋되지 않은 작업과 실제 DB(카드 301개)가 있어 제거 테스트 대상에서 제외.
- 제거 후에도 남는 Kanban 외 항목: Codex·Claude Code 로그·설정, Vault의 다른 노트·`.obsidian`, 브라우저의 사이트 데이터(화면 설정).

## 다른 Mac 설치용 실행 파일·가이드 — 2026-10-15

- 추가: `bin/kanban`(start·stop·restart·status·open·logs·backup·doctor·install-login·uninstall-login·foreground), Finder 더블클릭용 `Kanban.command`,
  [README 설치·실행 안내](../README.md#빠른-시작). 서버는 SIGTERM을 받으면 Ctrl+C처럼 정상 종료(수집·Obsidian 스레드 정리).
- 점검: 코드에 사용자 전용 경로 없음(README의 예시 경로만 제거). 외부 패키지 없음.
  Python **3.9.6(시스템) · 3.11 · 3.12**에서 테스트 63개 통과(DeprecationWarning을 오류로 취급해도 통과). macOS 기본 bash 3.2 문법 확인.
- 새 Mac 모사: 저장소 사본(.local·.git 제외) + 가짜 HOME(합성 Codex·Claude 로그) + 최소 PATH(/usr/bin:/bin)로
  doctor → start(셸 종료 후에도 실행 유지) → 중복 start 방지 → status(카드 2개 자동 수집) → stop(정상 종료·포트 해제) 확인.
- `Kanban.command` 실행(브라우저 열기 요청 확인), 포트 충돌 안내, `KANBAN_PYTHON`으로 Python 3.12 실행, 실행 중 `backup` 일관성 확인.
- 로그인 자동 실행: 임시 라벨로 실제 LaunchAgent 등록 → 실행 확인 → 강제 종료 시 launchd 재시작 확인 → stop/uninstall 후 작업·파일·포트 정리 확인.
- 발견·수정: 최소 PATH에서 `lsof`(/usr/sbin)를 못 찾아 포트 충돌을 놓치던 문제, 실패 시 이전 로그까지 보여 주던 문제,
  launchd 해제가 비동기라 직후 상태가 틀리던 문제, 자동 실행 사용자의 재시작 방식(`restart` 추가), 종료 후 DB 복사 안내 → `backup` 명령으로 대체.
- 실제 서비스: `bin/kanban backup` 후 기존 4310 프로세스를 종료하고 `bin/kanban start`로 재시작, 카드 301개 유지 확인.
- 미검증: 다른 실제 Mac(새 계정·Intel)에서의 설치, Vault가 문서/iCloud에 있을 때 LaunchAgent의 macOS 파일 접근 권한.

## 웹의 ‘새 작업’ 기능 제거 — 2026-10-15

- 제거: 상단 ‘+ 새 작업’, 대기 칸의 ‘+ 작업 추가’, 작업 작성·수정 창, 상세의 ‘수정’·‘복제해서 새 작업’·‘터미널에서 시작하기’(명령 복사),
  `POST /api/cards`·카드 내용 수정 API, 상태 응답의 `run_command`. 웹에서는 카드 보관만 변경 가능.
- 보드는 진행 중 · 완료 · 확인 필요 3칸(남아 있는 대기 카드는 확인 필요에 표시, 실제 DB에는 0개). CLI(`kanban run`, `recover`, `board`)는 유지.
- Python 테스트 63개 통과(HTTP 테스트를 생성 404·수정 400·보관 200으로 변경). 브라우저 `collection_smoke` 20개(새 작업 UI 없음·3칸 확인 추가),
  `grouping_smoke` 10개, `obsidian_smoke` 16개, `search_smoke` 12개 통과.
- 실제 서비스(DB 백업 후 재시작)에서 새 작업 버튼 없음, `POST /api/cards` 404, 3칸 보드 확인.

## 메인 화면 검색(검색어·기간) — 2026-10-15

- 기존 ‘작업 검색’은 카드의 최신 요청·답변만 검색하던 한계를 확인 → 모든 프롬프트(1,412개, 2.8MB)를 서버에서 검색하도록 교체(`kanban/search.py`, `GET /api/search`).
- 시안(`.local/search-mock/index.html`) A+B 적용: 검색 패널(돋보기는 입력칸 오른쪽 끝), 대상·범위, 기간 칩·직접 지정, 요약·초기화, 카드/프롬프트 보기, 상세의 일치 프롬프트 펼침·하이라이트·‘일치만 보기’.
- 수정한 문제: 옛 검색칸 CSS(왼쪽 33px 여백)가 남아 글자와 아이콘이 겹치던 문제 제거. 입력 직후 이전 결과를 새 검색어와 함께 요약에 표시하던 문제 → 결과마다 검색 조건을 기록해 일치할 때까지 ‘검색 중…’ 표시.
- Python 테스트 **63개 통과**(검색 3개: 전체 이력·하위 세션·단어 AND·대상·와일드카드 이스케이프·기간·수동 카드·검증·특정 프롬프트 조회·API).
- 브라우저 `search_smoke` **12개**, `collection_smoke` 20개, `grouping_smoke` 10개, `obsidian_smoke` 16개 통과.
- 실제 서비스(DB 백업 후 재시작): ‘결제’ 카드 4개·프롬프트 9개, ‘Obsidian’+최근 7일, 기간만(오늘), 프롬프트 목록 날짜 그룹 확인. 응답은 디바운스 0.3초 제외 수십 ms.

## 칩별 내 설명 작성·수정·삭제 — 2026-10-15

- 새 테이블 `user_descriptions`(기록된 설명 `catalog`과 분리), `POST /api/descriptions`(빈칸 = 삭제, 2,000자 제한, Origin 검증).
- 마우스 올리기: 기록된 설명 + 내 설명, 없으면 ‘설명이 없습니다. 클릭해서 작성해 주세요.’ 클릭/Enter/Space: 창 고정 후 작성·수정, 취소.
- 수정한 버그: ‘설명 작성’ 클릭 시 버튼이 편집기로 교체되면서 바깥 클릭으로 오인되어 창이 닫히던 문제.
- Python 테스트 **60개 통과**(저장·수정·빈칸 삭제·검증·재시작 유지, API). 브라우저 `collection_smoke` **20개** 2회 연속 통과
  (작성 → 수정 → 빈칸 삭제, HTML이 텍스트로만 표시됨), `grouping_smoke` 10개, `obsidian_smoke` 16개 통과.
- 실제 서비스(DB 백업 후 재시작): 데스크톱·390px 모바일에서 안내 문구와 편집기 표시, 화면 안에 들어오는지 확인. 취소만 눌러 실제 DB에는 아무 설명도 저장하지 않음.

## 설명 창의 외부 디렉터리 검색 링크 — 2026-10-15

- 검색 URL을 실제로 확인한 사이트만 사용: AugmentClaude `/search?q=`(스킬·에이전트·MCP·플러그인 통합, `frontend-design` 결과 확인),
  Smithery `/search?q=`(MCP, `chrome` 117건 확인)와 `/skills?q=`(스킬, `frontend-design` 143건 확인; Smithery는 MCP·스킬 제공, 에이전트 없음). 검색 URL이 문서화·동작하지 않은 Skillselion, Claude Market은 제외.
- 프로젝트 전용 스킬을 catalog `skill_scope`로 구분(실제 27개). 이름 붙인 팀 에이전트, Codex 하위 에이전트, 내부 ID, 기본 에이전트 유형은 링크 없음.
- 설명 창은 마우스를 칩에서 창으로 옮겨도 유지되어 링크를 누를 수 있음. 스크롤 시 닫지 않고 위치를 따라감(칩이 사라지거나 화면 밖이면 닫음).
- Python 테스트 59개, 브라우저 `collection_smoke` 19개(링크 URL·새 탭·noreferrer·기본 유형 링크 없음·창 유지 확인), `grouping_smoke` 10개, `obsidian_smoke` 16개 통과.
- 실제 서비스(DB 백업 후 재시작)에서 플러그인 에이전트·MCP·스킬은 링크, `impl-photo`(general-purpose) 같은 팀 에이전트는 링크 없음 확인.

## 스킬 / MCP / 에이전트 설명 툴팁 — 2026-10-15

- 설명 출처: Claude 로그의 `skill_listing`·`agent_listing_delta`·`mcp_instructions_delta`·`deferred_tools_delta`,
  설치된 `SKILL.md` frontmatter(프로젝트·홈·Codex 시스템·플러그인 경로). 새 `catalog` 테이블과 `/api/catalog`, 한 번의 백필.
- Claude가 내부 ID(`a0233…`)로 보낸 SendMessage 수신자를 `Agent` 호출 결과의 agentId로 이름/유형에 연결(파서 v5, 전체 재수집).
- 실제 데이터 적용 결과: 스킬 24/27(없음: `example`, `review`는 파일 없음, `orca-emulator`는 CLI 제공),
  Claude 에이전트 이름 94개 전부 설명 연결(직접 10, 유형 경유 84), MCP 도구 15/18, Codex 하위 에이전트 5개는 ‘암호화되어 표시 불가’ 안내.
- Python 테스트 **59개 통과**(catalog 3개, ID→이름 연결 1개 추가). 브라우저 `collection_smoke` **19개**(호버·포커스·탭 툴팁 추가) 3회 연속 통과,
  `grouping_smoke` 10개, `obsidian_smoke` 16개 통과.
- 수정한 테스트 경쟁 조건: 보관 직후 대화상자가 보드 갱신보다 먼저 닫혀 카드 수를 세던 단계가 간헐 실패 → 보드 갱신을 기다리도록 변경.
- 테스트 중 발견해 수정: 칩에 `data-provider`를 쓰면 도구 필터 탭 선택자와 충돌 → `data-owner`로 변경.
- 실제 서비스(4310, DB 백업 후 재시작)에서 Claude 카드의 하위 에이전트 칩에 마우스를 올려 유형과 설명이 표시되는 것을 확인.

## 스킬 / MCP / 에이전트 수집 점검 — 2026-10-15

실제 로그 301개(Codex 297, Claude 4)의 레코드 유형과 도구 호출을 전수 집계해 저장된 사용 내역과 대조했다.

- 화면의 예시 프롬프트(Codex, 중단): 시작 직후 `turn_aborted`. 그 사이 명령 3개는 이전 요청의 백그라운드 프로세스 종료
  기록(`unified_exec_startup`, failed)이라 이 요청의 사용이 아님 → ‘확인된 기록 없음’이 맞음.
- 실제로 없음: Codex MCP 호출 0건(내장 `clock`·`web`·`image_gen`만 존재), Codex 스킬 호출 도구 없음(`AGENTS.md`의 스킬 목록 290건은 사용 아님),
  Claude 스킬 호출 4건·슬래시 명령은 `/model` 등만 존재.
- 누락이었음(수정):
  - Codex `exec` 스크립트 안의 도구(`exec_command`, `apply_patch`, `view_image`, `write_stdin` 등)가 `exec` 하나로만 기록됨.
  - Codex 하위 에이전트에게 보낸 `send_message`·`followup_task`, `SubAgentActivity`가 에이전트로 기록되지 않음.
  - Claude `SendMessage` 수신자(실행 중인 팀 에이전트)가 기록되지 않음.
  - 구버전 수집 기록은 7일 창 밖 파일과 8MB 넘는 파일을 끝까지 다시 읽지 못함 → 알려진 파일은 끝까지 이어 읽도록 수정.
- 실제 DB 백업 후 재수집 결과(요청 수 변동 없음, 카드·보관·묶기 변경 0, 요청 삭제 0):
  Codex 에이전트 있는 요청 3 → 12, `exec`만 기록된 항목 442 → 4. Claude 에이전트 있는 요청 98 → 404.
- Python 테스트 **55개 통과**(신규 4개: 코드 모드 도구·하위 에이전트 메시지, Claude SendMessage, 파서 업그레이드 재수집, 큰 오래된 파일 이어 읽기).
  브라우저 `collection_smoke` 18개(4회 중 1회 타이밍 실패 후 재실행 통과), `grouping_smoke` 10개, `obsidian_smoke` 16개 통과.

## Obsidian 패널을 왼쪽 메뉴로 이동 — 2026-10-15

- 본문 상단의 Obsidian 패널을 왼쪽 메뉴 하단 OBSIDIAN 블록(상태 점·한 줄 상태·짧은 설명·연동 설정/지금 동기화)으로 이동.
  긴 설명·오류는 줄임 표시하고 마우스를 올리면 전체 문구, 상세는 연동 설정 창에서 확인. 모바일은 상단 메뉴 아래 한 줄.
- 낮은 화면(높이 820px 이하)에서는 고정 문구를 숨겨 프로젝트 목록 공간을 확보.
- 브라우저 `obsidian_smoke` 16개, `grouping_smoke` 10개, `collection_smoke` 18개 통과. 실제 서비스에서 데스크톱(1512×693)과
  390px 모바일 화면 확인, 가로 넘침 없음.

## 프로젝트별 Obsidian 연동과 자동 저장 / 선택 저장 — 2026-10-15

- 전역 Vault·자동 동기화 토글을 프로젝트별 설정(연동 안 함 / 자동 저장 / 선택 저장, 프로젝트별 Vault·상태·오류)으로 교체.
  새 테이블 `obsidian_projects`, `obsidian_picks`만 추가했고 기존 테이블·노트 기록은 유지.
- Python 테스트 **51개 통과**: 프로젝트별 Vault·모드·상태 독립, 선택 저장의 선택 프롬프트만 기록(원래 번호 유지)·추가 선택 반영·
  해제 시 노트 유지, 한 프로젝트 Vault 장애가 다른 프로젝트에 영향 없음, 선택 검증, 선택 프롬프트가 실행한 하위 세션만 포함.
- 브라우저: `obsidian_smoke.cjs` **16개**(프로젝트 행별 잘못된 경로 오류, 자동 저장, 두 번째 프로젝트 선택 저장, 프롬프트 선택→노트 생성,
  자동 저장 무클릭 반영, 재시작 유지, 프로젝트별 실패 표시·재시도, 편집 충돌·복원, 연동 끄기 후 노트 유지, 선택 해제 후 노트 유지, 모바일),
  `grouping_smoke.cjs` **10개**, `collection_smoke.cjs` **18개** 통과.
- 실제 서비스(4310): DB 백업 후 재시작. 모든 프로젝트가 ‘연동 안 함’ 상태로 시작하며 실제 Vault에는 아무것도 쓰지 않음.
  이전 Vault(`LLM-obsidian/kanban`)는 입력란에 미리 채워짐. 기존 노트 10개는 그대로.

## 에이전트 자동 실행 세션 묶기와 프롬프트 접기 — 2026-10-15

- 실제 데이터 분석: 세션 카드 287개 중 277개가 `codex exec` 자동 실행(worktree 261개). 같은 저장소에서 진행 중이던
  대화형 프롬프트를 찾는 방식으로 실제 DB 사본에서 **278/278 연결**, 재실행 시 추가 연결 0(멱등). 연결된 부모 프롬프트는 모두 셸 명령 사용.
- Python 테스트 **48개 통과**(신규 `tests/test_linking.py` 7개: 저장소 식별, codex exec·Claude SDK 연결, 모호·미연결 유지,
  셸 사용 우선·중첩 실행, 하위 폴더 `cd` 시 프로젝트 유지와 프로젝트 출처, Obsidian 상위 노트 포함·기존 노트 이동·편집본 유지·빈 폴더 정리).
- 브라우저: `grouping_smoke.cjs` **10개**, `obsidian_smoke.cjs` **15개**, `collection_smoke.cjs` **18개** 통과.
  실행 중인 하위 세션이 갱신돼도 펼친 프롬프트와 하위 세션 목록이 유지되는지 포함.
- 실제 서비스(4310, DB 백업 후 재시작): 카드 290개 중 280개 연결, 보드 표시 10개, 사이드바 프로젝트 144개 → 4개.
  실제 Chrome에서 120개 프롬프트 카드가 접힌 상태로 열리고 프롬프트별 하위 세션 수 표시, 펼침 유지 확인.
- 실제 Vault(`LLM-obsidian/kanban`)에서 웹의 ‘지금 동기화’ 실행: 표시 노트 283개 → 10개, 미편집 노트 273개를
  `Kanban/.kanban-merged/`로 이동(삭제 0, 편집본 0), 빈 프로젝트 폴더 75개 제거, 오류 0. 기존 `환영합니다!.md` 해시 동일.
  Obsidian 앱에서 Kanban 아래 4개 프로젝트 폴더와 상위 노트(프롬프트 120개, 속성 22개) 표시 확인.

## Obsidian 단방향 내보내기 — 2026-10-14

작업 전 상태를 다시 확인했다: Python 테스트 23개 통과, 4310 서버는 9월 26일 코드로 실행 중이었다.

- Python 테스트 **41개 통과** (기존 23개 + Obsidian 18개, `python3 -m unittest discover -s tests`).
  - 네이티브 Codex·Claude Code 로그 → 수집 → 내보내기, 같은 세션의 요청·답변 순서 연결
  - 45개 요청 전체 이력(API 첫 페이지 30개와 대조), 재동기화·동기화 기록 유실·재시작 후 중복 없음
  - 진행 중 → 완료 답변과 새 요청이 같은 노트에 반영, 설정·자동 동기화·마지막 결과의 재시작 유지, 구형 DB 마이그레이션
  - 한글·이모지·중첩 코드 펜스·`---`·YAML/Markdown/HTML 특수문자·CRLF·U+2028·100,000자 답변의 원문 보존
  - 잘못된 Vault 경로 10종 거부, 폴더 쓰기 실패·Vault 소실 시 오류 기록과 수집 지속, 권한 복구 후 재시도 성공
  - 사용자 편집·속성 삭제 시 원본 보존과 최신본 사본, 복원 시 편집본 백업, 사용자 파일·심볼릭 링크 우회
  - 보관·카드 삭제·연동 해제 후 노트 유지, 4개 동기화기 × 3 동시 실행에서 중복·임시 파일 없음, 이동한 노트 추적
  - 자동 동기화 스레드: 기본 꺼짐, 켜면 반영, 끄면 중단. HTTP API 입력 검증과 Origin 차단
  - 원시 도구 인자(URL·프롬프트)와 thinking 내용이 노트에 없는지 확인
- `node scripts/obsidian_smoke.cjs`: 실제 Chrome에서 버튼을 눌러 **15개 시나리오 통과**.
  미설정 안내 → 잘못된 경로 3종 오류 → 저장 → 지금 동기화 → 카드의 Obsidian 열기 링크 → 자동 동기화 켜기와 새 요청 반영 →
  서버 재시작 후 유지 → 쓰기 실패 표시(수집 계속) → 권한 복구 후 재시도 → 편집 충돌 표시와 복원 → 연동 해제 후 노트 유지 →
  기존 필터·상세 → 390px 모바일 → JavaScript 오류 없음.
- 기존 `node scripts/collection_smoke.cjs` **18개 통과**.
- 실제 DB 백업본 복사본 → 임시 Vault 전체 내보내기: 카드 283개 = 노트 283개, 노트별 요청 수 불일치 0,
  가장 큰 세션 508개 요청(1.9MB 노트) 포함. 첫 동기화 1.2초, 재동기화 0.02초(변경 없음 283). 복사본은 확인 후 삭제.
- 4310 서버는 `.local/backups/`에 DB를 백업한 뒤 해당 PID만 종료하고 새 코드로 재시작했다(`.local/server.log`).
  카드 283개 유지, 수집 정상, Obsidian은 미설정 상태. `node scripts/live_collection_check.cjs` 읽기 전용 검증 통과, 브라우저 오류 없음.
- 증빙: `.local/obsidian-qa/report.json`, `synced.png`, `error.png`, `conflict.png`, `detail.png`, `desktop.png`, `mobile.png`,
  `settings-mobile.png`, `sample-note.md`.

### 실제 Vault 연결 (사용자 지정: `/Users/leeyuhun/LLM-obsidian/kanban`)

- 실제 Chrome에서 4310 웹의 Vault 설정 → 저장 → 지금 동기화를 눌러 노트 283개 생성(오류·충돌 0).
- Vault의 기존 `환영합니다!.md`는 동기화 전후 SHA-256 동일. 추가된 항목은 `Kanban/` 폴더뿐.
- 카드 상세에 Obsidian 열기 링크와 노트 경로 표시 확인. 같은 `obsidian://open?path=` URL로 Obsidian 1.13.7이
  kanban Vault에서 해당 노트를 열었고, 속성 20개(체크박스·숫자 포함)와 제목·본문·코드 블록 렌더링을 화면으로 확인.
  Chrome의 외부 앱 열기 확인창은 자동화가 응답할 수 없어 링크 클릭 대신 동일 URL을 macOS `open`으로 실행했다.
- 보기만 한 뒤 283개 노트 모두 해시 일치(미편집). 자동 동기화는 꺼진 상태로 두었다.
- 확장 프로그램이 연 탭은 `Sec-Fetch-Site: cross-site`로 요청해 기존 보호 로직이 첫 화면까지 403으로 막았다.
  최상위 문서 이동(`/`)만 허용하고 모든 API·정적 하위 리소스·POST는 계속 차단하도록 고치고 테스트를 추가했다.
- 참고: Obsidian은 `kanban_id` 값(`codex:…`, `claude:…`)을 URI처럼 링크로 표시한다. 동작에는 영향이 없다.

## 동일 세션 요청·답변 보존 수정 — 2026-09-26

- 수정 전 재현: 같은 세션에 question-1/answer-1 → question-2/answer-2 입력 시 카드 하나의 내용만 교체되고 이전 이력은 없음.
- 세션 카드는 유지하면서 `session_turns`에 요청별 상태·질문·답변·사용량·시각을 별도 저장하도록 수정.
- 구형 수집 커서는 원본 로그 처음부터 재해석해 이력을 복구. 카드 ID와 보관 상태 유지.
- Python 테스트 **21개 통과**, 실제 Chrome 브라우저 시나리오 **13개 통과**.
- Codex: 두 번째 요청 후 보관된 카드에서 첫 질문·답변을 펼쳐 실제 텍스트 확인.
- Claude: 두 번째 응답이 도착하는 동안 첫 질문·답변을 펼친 상태 유지, 두 응답 내용 각각 확인.
- 동일 문구의 반복 질문, 서버 재시작, 기존 커서 이전, 35건 페이지 조회와 새 요청 도착 후 다음 페이지 검증.
- 실제 DB의 별도 사본에서 원본 로그 재수집: 이 대화의 요청 6개와 완료된 답변 5개 복구 확인(현재 요청은 진행 중).
- `.local/collection-qa/conversation-history.png`에 합성 대화의 화면 검증 저장.
- 테스트는 합성 로그와 실제 기존 로그의 읽기 전용 검증으로 수행했으며, 테스트용 모델 호출은 추가하지 않음.

## 자동 수집 추가 검증 — 2026-09-26

- Python 테스트 총 **18개 통과**: 기존 기능 9개 + 네이티브 수집 9개.
- `node scripts/collection_smoke.cjs`: 실제 Chrome 브라우저 검증 **11개 통과**.
- UI 등록 없이 로그 파일만 생성해 프로젝트와 카드 자동 등록 확인.
- 열린 상세 화면에서 완료 반영, 같은 세션 재개, 보관 상태 유지 확인.
- 일시정지 상태로 서버 재시작 후 설정 유지, 재개 시 누락분 수집 확인.
- 반복 스캔·서버 재시작의 중복 방지, 필터·보관함·모바일 가이드 확인.
- 부분 JSONL, 깨진 행, 오래된 이벤트 재생, 파일 교체, 하위 에이전트 제외 검증.
- 기존 로컬 로그를 읽어 실제 Codex 3개 / Claude Code 2개 세션 수집 확인.
- 원본 로그·CLI 설정·기존 프로젝트는 변경하지 않았다. 실제 모델 호출은 추가하지 않았다.
- 실제 DB 백업 후 기본 서버(4310)에 자동 수집 적용. 기존 프로젝트 등록 유지.
- `.local/collection-qa/report.json`, `desktop.png`, `mobile.png`에 합성 로그 기반 검증 증빙 저장.

아래는 자동 수집 도입 전 수동 카드 UI의 검증 기록이다.

검증일: 2026-09-25 (KST)

## 범위와 결과

- Python 단위·HTTP·CLI 통합 테스트 **9개 통과**.
- Orca 실제 브라우저 클릭 시나리오 **38개 통과**.
- 별도 Chrome 프로필에서 반응형·연결 복구 시나리오 **8개 통과**.
- 실행 명령과 결과의 실제 클립보드 내용 일치 확인.
- 모바일 390px에서 작업 작성·도구 선택·수정 진입·취소 확인.
- QA 서버 종료·재시작 후 프로젝트 2개, 카드 3개, 상태·결과·사용량 유지 확인.
- JavaScript 구문 검사, `git diff --check` 통과.

실제 모델 호출은 하지 않았다. Codex·Claude 실행 결과는 가짜 CLI로 검증했으며,
브라우저의 완료·실패·중단 표시는 전용 QA DB의 명시적 테스트 데이터로 검증했다.
실제 설치된 CLI의 인증·모델 응답·권한 승인 흐름은 검증 범위에 포함되지 않는다.

## 실제 클릭한 흐름

1. 프로젝트가 없는 상태의 새 작업, 첫 프로젝트 추가, 취소, 닫기.
2. 존재하지 않는 폴더 거부, 정상 등록, 중복 폴더 거부, 두 번째 프로젝트 등록.
3. Claude Code / Codex 선택, 빈 제목 거부, 작업 생성, 실행 명령 복사.
4. 대기 카드 제목·도구 수정, 복제하여 새 작업 만들기.
5. 두 도구 필터, 전체 도구, 검색 결과 없음, 필터 초기화, 제목 검색.
6. 보관, 보관함 조회, 보관 해제. 보관 카드의 편집·실행 안내 숨김.
7. 개별 프로젝트 보기와 전체 보기. HTML 입력이 실행되지 않고 텍스트로 표시됨.
8. 진행 중 카드 조회, 상세 화면을 연 상태에서 완료 결과 자동 갱신.
9. 결과 복사, 사용량·세션 정보 펼치기, 실패·중단 표시.
10. 사용 가이드 열기·닫기, 프로젝트 선택 후 새로고침.
11. 모바일 프로젝트 추가, 작업 폼, 가이드. 별도 Chrome에서 실제 viewport 크기 확인.
12. Escape로 모달 닫기, 서버 연결 실패 안내, 자동 재연결과 버튼 복구.

## 반응형 화면

Chrome의 실제 viewport를 1440, 1024, 768, 390, 360px로 지정해 확인했다.
본문의 가로 넘침이 없고 모바일 프로젝트 추가 버튼과 가이드가 보인다.
칸반 열은 좁은 화면에서 보드 안쪽으로 가로 스크롤한다.
데스크톱과 모바일의 첫 화면·카드가 있는 보드·상세·작업 작성 화면 PNG를 열어 확인했다.
Orca 내장 브라우저는 앱 패널 크기의 영향을 받으므로 정확한 반응형 크기는 별도 Chrome 검증을 기준으로 한다.

## 재현

```bash
python3 -m unittest discover -s tests -v
node --check web/app.js

# 별도 QA 서버 (새 QA DB를 사용해야 함)
KANBAN_DB=.local/browser-qa.sqlite3 python3 -m kanban serve --port 4311 --no-collect
# 별도 터미널
orca tab create --url http://127.0.0.1:4311 --json
python3 scripts/browser_smoke.py --page RETURNED_BROWSER_PAGE_ID

# Chrome 화면 검증 도구: 앱 실행에는 설치할 필요 없음
npm install --prefix .local/browser-tools --no-audit --no-fund playwright
# 기본 빈 작업 공간 서버(4310)가 실행 중인 상태에서
node scripts/responsive_smoke.cjs
# 위 브라우저 QA의 테스트 데이터와 4311 서버가 남아 있는 상태에서
node scripts/populated_smoke.cjs
```

Chrome 기본 경로는 macOS `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`이다.
반응형 스크립트에서는 `KANBAN_CHROME`으로 다른 경로를 지정할 수 있다.
검증 도구는 별도 임시 Chrome 프로필을 사용한다.

## 로컬 증빙

Git에는 테스트 코드와 이 문서만 남기고, QA 데이터와 이미지는 `.local` 아래에 둔다.

- `.local/browser-qa-report.json`: 브라우저 클릭 검증 결과
- `.local/visual-qa/report.json`: 반응형 검증 결과
- `.local/visual-qa/welcome-1440.png`, `welcome-390.png`: 첫 화면
- `.local/visual-qa/board-desktop.png`, `board-mobile.png`: 테스트 카드가 있는 보드
- `.local/visual-qa/detail-desktop.png`, `new-task-mobile.png`: 상세와 모바일 작성 화면

실제 사용 DB `.local/kanban.sqlite3`에는 QA 프로젝트나 카드를 넣지 않았다.
기존 `unified-kanban`의 Git 상태는 작업 전후 동일했다.
이 작업에서 기존 프로젝트 파일·DB·서비스·설정을 변경하지 않았다.


## 배경 클릭 닫기와 요청별 사용 내역

- Python 23개, 브라우저 시나리오 16개 통과.
- 실제 Chrome에서 배경 클릭 닫기, 내부 클릭 유지, 내부에서 외부로 선택 드래그 유지, 모바일 안내창 닫기를 검증.
- Codex 모델·도구·스킬 참조와 Claude 모델·하위 에이전트·명시적 스킬 호출 표시를 검증.
- 다음 요청에 이전 도구·스킬이 섞이지 않고 이전 대화 이력에 남는지 검증.
- 합성 로그와 임시 DB 사용, 유료 모델 호출 없음.

## 대화별 사용 내역 UI

- 대화 요약에 스킬·MCP·에이전트 배지를 표시하고 펼치면 해당 요청·답변·전체 항목을 표시.
- 최신 요청·답변 중복 표시는 제거하고 MCP 서버/도구를 일반 도구와 분리.
- 브라우저 17개 시나리오 통과. 실제 서비스의 데스크톱 및 390px 모바일 상세창 표시와 가로 넘침 확인.

## 프롬프트별 요청·답변·사용 내역 묶음

브라우저 18개 시나리오 통과. 접기 없이 요청 → 답변 → 스킬/MCP/에이전트 순서와 세션 ID 표시를 검증했습니다. 실제 서비스의 데스크톱·390px 모바일 레이아웃도 확인했습니다.

## Codex / Claude Code 재검증 (2026-09-27)

- 실행 중인 실제 서비스에서 Codex 3개 세션(16개 요청), Claude Code 2개 세션(첫 페이지 각 30개 요청)의 요청·답변·세션 표시를 API 기록과 대조.
- Claude 두 세션의 이전 기록 더 보기, 중복 방지, 양쪽 모바일 가로 넘침과 배경 클릭 닫기를 확인. 브라우저 오류 없음.
- `node scripts/live_collection_check.cjs`로 실제 서비스 읽기 전용 검증을 재실행할 수 있습니다. 결과는 `.local/collection-qa/live-verification.json`에 저장합니다.
- 별도 합성 로그 테스트는 Codex MCP·하위 에이전트와 Claude MCP·스킬·하위 에이전트의 요청별 표시까지 검증합니다. 새 유료 모델 호출은 수행하지 않습니다.
