import argparse
import json
import sys

from .store import Store


def main():
    parser = argparse.ArgumentParser(description='Kanban · Independent Claude Code / Codex Kanban')
    commands = parser.add_subparsers(dest='command', required=True)
    server = commands.add_parser('serve', help='Start the local dashboard')
    server.add_argument('--port', type=int, default=31229)
    server.add_argument('--no-collect', action='store_true', help='Disable native session collection for this server')
    commands.add_parser('collect', help='Collect native Codex / Claude Code sessions once')
    args = parser.parse_args()
    if args.command == 'serve':
        from .server import serve
        serve(args.port, collect=not args.no_collect)
        return 0
    from .collector import Collector
    print(json.dumps(Collector(Store()).scan(), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
