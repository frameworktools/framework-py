"""Command-line entry points: ``python -m framework``.

    python -m framework create --from-layers FILE --path DIR [--interactive]
    python -m framework create --name NAME --path DIR [--type TYPE] [--workflow ID]
        [--tool ID[=false]]... [--skill-set ID]... [--package-set ID]... [--interactive]
        [--offline]
    python -m framework adopt [--path DIR] [--yes] [--type TYPE] [--sensitive]
        [--private FOLDER ...] [--cloud [--slug SLUG]] [--force]
    python -m framework upgrade [--path DIR] [--apply] [--no-backup] [--type TYPE]
        [--move-settings]
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
        help="project type, with --name (default: project, or asked with --interactive)",
    )
    create.add_argument(
        "--workflow",
        choices=["jupyter", "quarto", "scripts"],
        help="with --name, how notebooks are written (default: jupyter for project types)",
    )
    create.add_argument(
        "--tool",
        action="append",
        default=[],
        metavar="ID[=false]",
        help="with --name, turn a tool on, or off with =false (repeatable)",
    )
    create.add_argument(
        "--skill-set", action="append", default=[], metavar="ID", help="add a skill set"
    )
    create.add_argument(
        "--package-set", action="append", default=[], metavar="ID", help="add a package set"
    )
    create.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "with --name, ask for the type when --type is missing (the project is "
            "registered and gets a git repository either way, unless your defaults turn "
            "git off); with --from-layers, initialize git and register the project "
            "instead of running headless"
        ),
    )

    create.add_argument(
        "--offline",
        action="store_true",
        help="with --name, build from Framework's built-in defaults without Framework Cloud",
    )

    adopt = commands.add_parser("adopt", help="make an existing folder a Framework project")
    adopt.add_argument("--path", default=".", help="the project folder (default: .)")
    adopt.add_argument("--yes", action="store_true", help="accept the proposed folders")
    adopt.add_argument(
        "--type",
        choices=["project", "project_sensitive", "course", "presentation", "bare"],
        help="the project type (default: inferred from the folders)",
    )
    adopt.add_argument(
        "--sensitive",
        action="store_true",
        help="the project holds sensitive data: turn on Framework's guards for private folders",
    )
    adopt.add_argument(
        "--cloud",
        action="store_true",
        help="after adopting, create the project on Framework Cloud and link it",
    )
    adopt.add_argument(
        "--slug", help="with --cloud: the project's slug (default: made from the folder's name)"
    )
    adopt.add_argument(
        "--force",
        action="store_true",
        help="adopt the folder as Python even when its files say another language",
    )
    adopt.add_argument(
        "--private",
        action="append",
        metavar="FOLDER",
        help="map a folder to a private key, keeping its path (repeatable)",
    )

    upgrade = commands.add_parser(
        "upgrade", help="bring a project made by an older Framework to the current layout"
    )
    upgrade.add_argument("--path", default=".", help="the project folder (default: .)")
    upgrade.add_argument(
        "--apply", action="store_true", help="apply the plan (default: print it only)"
    )
    upgrade.add_argument(
        "--no-backup",
        action="store_true",
        help="don't copy the files it rewrites to .framework/backups/ first",
    )
    upgrade.add_argument(
        "--type",
        choices=("project", "project_sensitive", "course", "presentation", "bare"),
        help="change the project's type (project_sensitive makes it sensitive)",
    )
    upgrade.add_argument(
        "--move-settings",
        action="store_true",
        help="move settings keys outside default: into settings/<key>.yml",
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
    name_only = args.command == "create" and (
        args.type
        or args.workflow
        or args.tool
        or args.skill_set
        or args.package_set
        or args.offline
    )
    if name_only and args.from_layers:
        parser.error(
            "--type, --workflow, --tool, --skill-set, --package-set, and --offline need --name"
        )
    try:
        if args.command == "adopt":
            from framework.adopt import adopt

            path = adopt(
                args.path,
                yes=args.yes,
                type=args.type,
                sensitive=True if args.sensitive else None,
                cloud=True if args.cloud else None,
                slug=args.slug,
                force=args.force,
                private=args.private,
            )
        elif args.command == "upgrade":
            from framework.upgrade import upgrade

            upgrade(
                args.path,
                dry_run=not args.apply,
                backup=not args.no_backup,
                type=args.type,
                move_settings=args.move_settings,
            )
            return 0
        elif args.command == "bootstrap":
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

            tools = {}
            for tool in args.tool:
                name, given, value = tool.partition("=")
                switch = value.lower()
                if given and switch not in ("true", "yes", "1", "on", "false", "no", "0", "off"):
                    parser.error(f"--tool {tool}: use {name}, {name}=true, or {name}=false")
                tools[name] = switch not in ("false", "no", "0", "off")
            path = new(
                args.name,
                args.path,
                args.type,
                workflow=args.workflow,
                tools=tools,
                package_sets=args.package_set,
                skill_sets=args.skill_set,
                interactive=args.interactive,
                offline=args.offline,
            )
    except FrameworkError as error:
        # The functions' advice names Python arguments; the command line has flags
        message = (
            str(error)
            .replace("Pass yes=True.", "Pass --yes.")
            .replace("pass force=True", "pass --force")
        )
        print(f"Error: {message}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
