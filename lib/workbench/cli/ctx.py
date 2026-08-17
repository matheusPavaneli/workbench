"""``wb ctx`` -- inspect and manage the central configuration.

Configuration lives in ``~/.workbench`` and is matched to repos by rule, so a
new checkout needs no setup. These commands are the only writers of that
directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import artifacts, contexts, gitctx, providers
from ..errors import ConfigError, UsageError

ACTIONS = ["show", "list", "add", "use", "test"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("ctx", help="configuration and account contexts")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    actions.add_parser("show", help="print the context resolved for this repo, and why")
    actions.add_parser("list", help="list defined contexts")

    add = actions.add_parser("add", help="define a context")
    add.add_argument("name")
    add.add_argument("--provider", required=True, choices=providers.names())
    add.add_argument("--base-url", required=True, help="Jira site root, or https://dev.azure.com/<org>")
    add.add_argument("--project", required=True, help="Jira project key, or Azure DevOps project name")
    add.add_argument("--pat-env", help="name of the environment variable holding the token")
    add.add_argument("--pat-keychain", help="OS keychain account name (macOS/Linux only)")
    add.add_argument("--email", help="Jira account email; Jira authenticates as email:api_token")
    add.add_argument("--preset", default="startup", choices=contexts.PRESETS)
    add.add_argument("--git-name", help="commit author name for repos in this context")
    add.add_argument("--git-email", help="commit author email for repos in this context")
    add.add_argument("--board", help="default board id for 'wb task list'")
    add.add_argument("--force", action="store_true", help="overwrite an existing context")

    use = actions.add_parser("use", help="bind this repo (or repos like it) to a context")
    use.add_argument("name")
    use.add_argument(
        "--remember",
        choices=["repo", "remote", "path"],
        default="repo",
        help="repo: write .workflow/config.json; remote/path: add a rule for every repo like this one",
    )

    actions.add_parser("test", help="verify the resolved context against the tracker (1 request)")


def run(args: argparse.Namespace) -> int:
    handlers = {"show": _show, "list": _list, "add": _add, "use": _use, "test": _test}
    if not args.action:
        raise UsageError("wb ctx needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return handlers[args.action](args)


def _show(_: argparse.Namespace) -> int:
    resolution = contexts.resolve()
    context = resolution.context
    print(f"context   {context.name}   (from {resolution.source})")
    print(f"provider  {context.provider}")
    print(f"base_url  {context.base_url}")
    print(f"project   {context.project}")
    print(f"preset    {context.preset}")
    if context.board:
        print(f"board     {context.board}")
    if context.git:
        print(f"git       {context.git.get('name', '?')} <{context.git.get('email', '?')}>")
    source = context.auth.get("pat_env") or context.auth.get("pat_keychain") or "<none>"
    print(f"token     from {source}")
    return 0


def _list(_: argparse.Namespace) -> int:
    names = contexts.available()
    if not names:
        print(f"no contexts in {contexts.contexts_dir()}")
        print("define one: wb ctx add <name> --provider jira --base-url URL --project KEY --pat-env VAR")
        return 0
    for name in names:
        context = contexts.load(name)
        print(f"{name:<16} {context.provider:<6} {context.base_url}  [{context.preset}]")
    return 0


def _add(args: argparse.Namespace) -> int:
    if bool(args.pat_env) == bool(args.pat_keychain):
        raise UsageError(
            "give exactly one of --pat-env / --pat-keychain",
            fix=["--pat-env NAME is the portable choice; the token stays in the environment"],
        )
    if args.provider == "jira" and not args.email:
        raise UsageError(
            "Jira contexts need --email",
            fix=["Jira Cloud authenticates as email:api_token"],
        )

    path = contexts.contexts_dir() / f"{args.name}.json"
    if path.exists() and not args.force:
        raise ConfigError(f"context {args.name!r} already exists", fix=[f"pass --force to overwrite {path}"])

    auth: dict[str, str] = {}
    if args.pat_env:
        auth["pat_env"] = args.pat_env
    if args.pat_keychain:
        auth["pat_keychain"] = args.pat_keychain
    if args.email:
        auth["email"] = args.email

    data: dict[str, object] = {
        "provider": args.provider,
        "base_url": args.base_url.rstrip("/"),
        "project": args.project,
        "preset": args.preset,
        "auth": auth,
    }
    if args.board:
        data["board"] = args.board
    if args.git_name or args.git_email:
        data["git"] = {k: v for k, v in (("name", args.git_name), ("email", args.git_email)) if v}

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    if args.pat_env:
        print(f"next: set {args.pat_env} in the environment, then run: wb ctx test")
    return 0


def _use(args: argparse.Namespace) -> int:
    contexts.load(args.name)  # fail early if the context does not exist
    cwd = Path.cwd().resolve()

    if args.remember == "repo":
        root = gitctx.repo_root(cwd) or cwd
        path = root / contexts.REPO_CONFIG
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"context": args.name}, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}")
        print(f"add {artifacts.WORKFLOW_DIR}/ to .gitignore, except config.json, if you want to share it")
        return 0

    if args.remember == "remote":
        remote = gitctx.origin(cwd)
        if remote is None:
            raise UsageError(
                "this repo has no origin remote to match on",
                fix=["use --remember path, or --remember repo for this checkout only"],
            )
        match: dict[str, str] = {"remote_host": remote.host, "org": remote.org}
        described = f"{remote.host}/{remote.org}"
    else:
        match = {"path_prefix": str(cwd)}
        described = str(cwd)

    _append_rule(match, args.name)
    print(f"rule added: {described} -> {args.name}  ({contexts.rules_path()})")
    return 0


def _append_rule(match: dict[str, str], name: str) -> None:
    path = contexts.rules_path()
    rules: list[dict[str, object]] = []
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ConfigError(f"{path} must contain a JSON array", fix=["fix or delete the file"])
        rules = loaded
        for rule in rules:
            if isinstance(rule, dict) and rule.get("match") == match:
                rule["context"] = name
                path.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")
                return

    rules.append({"match": match, "context": name})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")


def _test(_: argparse.Namespace) -> int:
    resolution = contexts.resolve()
    provider = providers.for_context(resolution.context)
    identity = provider.probe()
    print(f"ok  {resolution.context.name} ({resolution.context.provider}) authenticated as {identity.account}")
    print(f"    {identity.detail}")
    return 0
