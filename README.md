# Kanban-board

Codex와 Claude의 작업을 모아 보는 독립 로컬 칸반 대시보드입니다.
기존 `unified-kanban`과는 별도 저장소·폴더·DB를 사용하며 Hermes 설치나 실행이 필요하지 않습니다.

## 실행

Python 3.9 이상이 필요합니다. 대시보드는 추가 패키지 없이 실행됩니다.

```bash
cd /Users/leeyuhun/cursor-project/Kanban-board
python3 -m kanban serve
```

브라우저에서 <http://127.0.0.1:4310>을 엽니다. 종료는 Ctrl+C입니다.
다른 포트는 `python3 -m kanban serve --port 4311`로 지정합니다.
데이터는 프로젝트 내부 `.local/kanban.sqlite3`에 저장되고 Git에 포함되지 않습니다.
`KANBAN_DB`로 저장 위치를 바꿀 수 있습니다. CLI와 서버에 같은 경로를 지정하세요.

## 작업 사용 방법

1. UI의 프로젝트 추가에서 실제 작업 폴더의 절대 경로를 등록합니다.
2. 새 작업을 만들고 Codex 또는 Claude를 선택합니다.
3. 카드 상세에 표시되는 `python3 -m kanban start CARD_ID`를 이 저장소 폴더에서 실행합니다.
4. 진행 중 상태, 최종 결과, 관측된 토큰 사용량을 UI에서 확인합니다.

터미널에서 바로 생성하고 실행할 수도 있습니다.

```bash
python3 -m kanban board 'My project' /absolute/path/to/project
python3 -m kanban boards
python3 -m kanban run codex --board BOARD_ID '이 프로젝트의 구조를 설명해줘'
python3 -m kanban run claude --board BOARD_ID '이 프로젝트의 테스트 구성을 설명해줘'
```

Codex와 Claude CLI는 별도로 설치·로그인되어 있어야 합니다. 래퍼는 해당 CLI의 기존 설정과 권한을 따르며 권한 우회 옵션을 추가하지 않습니다. 기존 CLI hooks도 실행될 수 있습니다. 이 프로젝트는 기존 설정 파일을 수정하지 않습니다.

## 현재 구현 범위

- 독립 UI/API, 프로젝트별 보드, 검색, 도구 필터, 보관/해제
- Todo → Running → Done / Failed / Interrupted 기록
- 전용 래퍼로 실행한 비대화형 Codex·Claude 작업의 최종 결과와 사용량 저장
- UI를 종료해도 CLI는 자체 DB에 기록. 서버 재시작 후 기록 유지
- Ctrl+C 시 하위 프로세스 종료 및 중단 기록

**일반 `codex` / `claude` 대화형 세션, IDE, Orca에서 시작한 작업은 아직 자동 수집하지 않습니다.**
UI는 작업 기록을 만들고 조회하며, 에이전트 실행은 명시적인 CLI 명령으로 합니다.
실행 상태는 드래그하거나 UI에서 임의로 완료 처리하지 않습니다.
사용량은 CLI가 반환한 원본 필드만 표시하고, 모델·일자별 사용량은 추정하지 않습니다.
프로세스 강제 종료나 전원 중단으로 Running에 남은 카드는 실행 종료를 확인한 후
`python3 -m kanban recover CARD_ID`로 중단 기록으로 복구합니다. 이 명령은 프로세스를 종료하지 않습니다.

Obsidian 연동과 기존 데이터 가져오기는 아직 구현하지 않았습니다. 후속 범위는 [개발 계획](docs/PLAN.md)에 정리했습니다.

## 검증

```bash
python3 -m unittest discover -s tests -v
node --check web/app.js
```

테스트는 임시 DB와 가짜 CLI를 사용합니다. 실제 모델 호출·과금은 하지 않으며 실제 CLI 로그인과 모델 응답은 별도 검증이 필요합니다.

## 로컬 데이터

서버는 `127.0.0.1`에만 바인딩하며 Host/Origin 검증과 JSON 요청 제한을 적용합니다.
로컬 단일 사용자용으로, 외부 네트워크 공개용 인증 서버가 아닙니다.
요청과 최종 결과는 로컬 DB에 저장됩니다. DB를 공유하기 전에 내용을 확인하세요.
백업은 실행과 서버를 종료한 뒤 `.local` 폴더 전체를 복사합니다.

## 연결 방식 참고

- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode): `codex exec --json --output-last-message`
- [Claude programmatic execution](https://code.claude.com/docs/en/headless): `claude -p --output-format json`
