"""Command-line entry points: ``python -m framework``.

    python -m framework create --from-layers FILE --path DIR [--interactive]
    python -m framework create --name NAME --path DIR [--type TYPE] [--interactive]
    python -m framework bootstrap [--path DIR] [--interactive] [--no-packages]
    python -m framework hook pre-commit

Exit codes: 0 on success, 1 on failure (the message is on stderr), and 2 for
a usage error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from framework.errors import FrameworkError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m framework", description="Framework for Python")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create a Python project")
    source = create.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--from-layers", metavar="FILE", help="a layers file (framework.pub format)"
    )
    source.add_argument("--name", help="project name; your defaults apply, as with framework.new()")
    create.add_argument("--path", required=True, help="the project folder to create")
    create.add_argument(
        "--type",
        choices=["project", "project_sensitive", "course", "presentation", "bare"],
        help="project type, with --name (default: bare, or asked with --interactive)",
    )
    create.add_argument(
        "--interactive",
        action="store_true",
        help="prompt for missing answers, initialize git, and register the project",
    )

    bootstrap = commands.add_parser("bootstrap", help="prepare a cloned project")
    bootstrap.add_argument("--path", default=".", help="the project folder (default: .)")
    bootstrap.add_argument("--interactive", action="store_true", help="ask for empty .env values")
    bootstrap.add_argument(
        "--no-packages", action="store_true", help="skip installing packages with uv"
    )

    hook = commands.add_parser("hook", help="run a git hook's checks (called by the hook)")
    hook.add_argument("name", choices=["pre-commit"])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "hook":
        from framework.hooks import hook_run

        return hook_run(args.name)
    if args.command == "create" and args.from_layers and args.type:
        parser.error("--type can only be used with --name")
    try:
        if args.command == "bootstrap":
            from framework.bootstrap import bootstrap

            path = bootstrap(
                args.path, interactive=args.interactive, packages=not args.no_packages
            ).root
        elif args.from_layers:
            from framework.create import project_create_from_layers

            path = project_create_from_layers(
                args.from_layers, args.path, interactive=args.interactive
            )
        else:
            from framework.create import new

            path = new(args.name, args.path, args.type, interactive=args.interactive)
    except FrameworkError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
