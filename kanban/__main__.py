import argparse
import json
import sys

from .store import Store


def main():
    parser = argparse.ArgumentParser(description='Independent Codex / Claude Kanban')
    commands = parser.add_subparsers(dest='command', required=True)
    server = commands.add_parser('serve', help='Start the local dashboard')
    server.add_argument('--port', type=int, default=4310)
    commands.add_parser('boards', help='List project IDs')
    board = commands.add_parser('board', help='Register a project directory')
    board.add_argument('name')
    board.add_argument('directory')
    run = commands.add_parser('run', help='Create and run an observed task')
    run.add_argument('provider', choices=['codex', 'claude'])
    run.add_argument('--board', required=True)
    run.add_argument('--title')
    run.add_argument('prompt')
    start = commands.add_parser('start', help='Run a Todo card created in the UI')
    start.add_argument('card_id')
    recover = commands.add_parser('recover', help='Mark an orphaned run interrupted; does not stop a process')
    recover.add_argument('card_id')
    args = parser.parse_args()
    if args.command == 'serve':
        from .server import serve
        serve(args.port)
        return 0
    store = Store()
    if args.command == 'boards':
        print(json.dumps(store.boards(), ensure_ascii=False, indent=2))
    elif args.command == 'board':
        print(json.dumps(store.create_board(args.name, args.directory), ensure_ascii=False, indent=2))
    elif args.command == 'recover':
        if store.card(args.card_id)['status'] != 'running':
            raise ValueError('Running 카드만 복구할 수 있습니다.')
        store.update(args.card_id, status='interrupted', result='사용자가 종료된 실행의 기록을 복구했습니다.')
    else:
        from .runner import run
        identifier = args.card_id if args.command == 'start' else store.create_card(
            args.board, args.title or args.prompt[:100], args.prompt, args.provider)['id']
        print('Card: ' + identifier, flush=True)
        return run(store, identifier)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
