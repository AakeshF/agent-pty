from __future__ import annotations

import argparse
import sys

from agent_pty import (
    KeyParseError,
    Pty,
    SessionExistsError,
    SessionNotFoundError,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-pty",
        description="Persistent PTY tool for LLM coding agents (tmux-backed).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("spawn", help="Create a new session")
    p.add_argument("name")
    p.add_argument("--cmd", default=None, help="Command to run (default: shell)")
    p.add_argument("--cwd", default=None, help="Working directory")
    p.add_argument("--cols", type=int, default=80)
    p.add_argument("--rows", type=int, default=24)

    p = sub.add_parser("send", help="Send keys to a session")
    p.add_argument("name")
    p.add_argument("text")

    p = sub.add_parser("snapshot", help="Print current screen of a session")
    p.add_argument("name")

    p = sub.add_parser("wait-for", help="Block until pattern appears")
    p.add_argument("name")
    p.add_argument("pattern")
    p.add_argument("--timeout", type=float, default=10.0)

    p = sub.add_parser("kill", help="Kill a session")
    p.add_argument("name")

    sub.add_parser("list", help="List managed sessions")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "spawn":
            print(
                Pty.spawn(
                    args.name,
                    cmd=args.cmd,
                    cwd=args.cwd,
                    cols=args.cols,
                    rows=args.rows,
                )
            )
        elif args.subcommand == "send":
            Pty.send(args.name, args.text)
        elif args.subcommand == "snapshot":
            print(Pty.snapshot(args.name))
        elif args.subcommand == "wait-for":
            print(Pty.wait_for(args.name, args.pattern, timeout=args.timeout))
        elif args.subcommand == "kill":
            Pty.kill(args.name)
        elif args.subcommand == "list":
            for n in Pty.list():
                print(n)
    except (
        SessionExistsError,
        SessionNotFoundError,
        KeyParseError,
        TimeoutError,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
