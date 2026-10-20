#!/bin/bash
# Double-click in Finder: starts Kanban in the background and opens it in the browser.
cd "$(dirname "$0")" || exit 1
./bin/kanban open
status=$?
echo
if [ $status -eq 0 ]; then
  echo "이 창을 닫아도 Kanban은 계속 실행됩니다. 종료하려면: bin/kanban stop"
else
  echo "실행하지 못했습니다. 위 메시지와 'bin/kanban doctor' 결과를 확인하세요."
fi
[ -t 0 ] && read -r -n 1 -s -p "아무 키나 누르면 창이 닫힙니다." && echo
exit $status
