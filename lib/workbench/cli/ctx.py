"""``wb ctx`` -- inspect and manage the central configuration.

Configuration lives in ``~/.workbench`` and is matched to repos by rule, so a
new checkout needs no setup. These commands are the only writers of that
directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import anonymise, artifacts, contexts, gitctx, providers
from ..errors import ConfigError, UsageError

ACTIONS = ["show", "list", "add", "use", "test", "record"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("ctx", help="configuration and account contexts")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    actions.add_parser("show", help="print the context resolved for this repo, and why")
    actions.add_parser("list", help="list defined contexts")

    add = actions.add_parser("add", help="define a context")
    add.add_argument("name")
    add.add_argument("--provider", required=True, choices=providers.names())
    add.add_argument("--base-url", help="Jira site root, or https://dev.azure.com/<org>; not used by github/local")
    add.add_argument("--project", help="Jira project key, Azure project name, or GitHub owner/repo")
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

    test = actions.add_parser("test", help="verify the resolved context against the tracker (1 request)")
    test.add_argument("--deep", action="store_true", help="also report fields this tool is discarding")

    record = actions.add_parser("record", help="save an anonymised copy of this tracker's payloads as fixtures")
    record.add_argument("key", help="a real ticket to record; its content is replaced, its shape is kept")
    record.add_argument("--out", help="directory to write to (default: tests/fixtures/<provider>/local)")


def run(args: argparse.Namespace) -> int:
    handlers = {"show": _show, "list": _list, "add": _add, "use": _use, "test": _test, "record": _record}
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
    required = contexts.REQUIRED_FIELDS[args.provider]
    missing = [f"--{field.replace('_', '-')}" for field in required if not getattr(args, field, None)]
    if missing:
        raise UsageError(
            f"a {args.provider} context needs {', '.join(missing)}",
            fix=[f"required for {args.provider}: {', '.join('--' + f.replace('_', '-') for f in required)}"],
        )

    # A local backlog has no credential, and github can borrow gh's. Everything
    # else must say where its token lives before it is written down.
    if args.provider == "local":
        if args.pat_env or args.pat_keychain:
            raise UsageError(
                "a local context has no credential to resolve",
                fix=["drop --pat-env / --pat-keychain; the local provider makes no requests"],
            )
    elif args.provider == "github":
        if args.pat_env and args.pat_keychain:
            raise UsageError("give at most one of --pat-env / --pat-keychain", fix=["or neither, to use gh's token"])
    elif bool(args.pat_env) == bool(args.pat_keychain):
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
        "base_url": (args.base_url or contexts.DEFAULT_BASE_URL.get(args.provider, "")).rstrip("/"),
        "project": args.project or "",
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
    elif args.provider == "github":
        print("next: gh auth login (its token is used when the context names none), then: wb ctx test")
    elif args.provider == "local":
        print('next: wb ctx use ' + args.name + ' && wb task new "the first thing to do"')
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


def _test(args: argparse.Namespace) -> int:
    resolution = contexts.resolve()
    provider = providers.for_context(resolution.context)
    identity = provider.probe()
    print(f"ok  {resolution.context.name} ({resolution.context.provider}) authenticated as {identity.account}")
    print(f"    {identity.detail}")
    if getattr(args, "deep", False):
        _discarded(provider)
    return 0


# Fixture names, keyed by what the request looked like. They match the files
# that ship with the package, so a recording drops straight into the same slots
# the suite already reads.
_JIRA_SHAPES = (
    ("/comment", "comments"),
    ("changelog", "changelog"),
    ("/search/jql", "search"),
    ("/issue/", "issue"),
    ("/myself", "probe"),
)
_AZURE_SHAPES = (
    ("/comments", "comments"),
    ("/updates", "updates"),
    ("/wiql", "wiql"),
    ("/workitems/", "workitem"),
    ("/_apis/wit/workitems", "batch"),
)
_GITHUB_SHAPES = (
    ("/timeline", "timeline"),
    ("/comments", "comments"),
    ("/issues/", "issue"),
    ("/issues", "list"),
    ("/user", "probe"),
)
_SHAPES = {"jira": _JIRA_SHAPES, "azure": _AZURE_SHAPES, "github": _GITHUB_SHAPES}


def _record(args: argparse.Namespace) -> int:
    """Save this tenant's payload shapes, with the content replaced.

    The fixtures that ship follow the vendors' published contracts, which cannot
    describe a custom field somebody added in 2019 -- and that is exactly what
    breaks first for a new user. The README asked people to close that gap by
    hand with anonymised payloads, which is a request nobody acts on. This is
    the same request as one command.
    """
    key = artifacts.validate_key(args.key)
    resolution = contexts.resolve()
    context = resolution.context
    if context.provider == "local":
        raise UsageError(
            "the local provider has no payloads to record",
            fix=["point at a tracker first: wb ctx use <name>"],
        )

    captured: list[tuple[str, object]] = []
    _capture(provider := providers.for_context(context), captured)

    # The calls triage makes. Failures are reported and skipped: a tenant that
    # forbids one endpoint should still get fixtures for the rest.
    for label, call in (
        ("task", lambda: provider.fetch_task(key)),
        ("comments", lambda: provider.fetch_comments(key, 20)),
        ("history", lambda: provider.fetch_history(key, 20)),
        ("probe", provider.probe),
    ):
        try:
            call()
        except Exception as exc:  # noqa: BLE001 - a partial recording beats none
            print(f"skipped {label}: {type(exc).__name__}")

    out = Path(args.out) if args.out else _default_out(context.provider)
    written = _write_fixtures(captured, context.provider, out)

    if not written:
        raise UsageError(
            "nothing was recorded",
            fix=[f"check the ticket exists and this context can read it: wb task get {key}"],
        )

    print(f"wrote {len(written)} fixture(s) to {out}")
    for name in sorted(written):
        print(f"  {name}.json")
    print()
    print("content replaced, shape kept. Read one before committing it.")
    print("the suite picks these up automatically: PYTHONPATH=\"lib;tests\" python -m unittest discover -s tests")
    return 0


def _capture(provider, captured: list) -> None:
    """Tee every transport response, without changing what the provider does."""
    for name in ("get", "post"):
        original = getattr(provider, name)

        def tee(*a, _original=original, **kw):
            response = _original(*a, **kw)
            captured.append((str(a[0]) if a else "", response))
            return response

        setattr(provider, name, tee)


def _write_fixtures(captured: list, provider: str, out: Path) -> list[str]:
    shapes = _SHAPES.get(provider, ())
    anonymiser = anonymise.Anonymiser()
    written: dict[str, object] = {}

    for path, response in captured:
        name = next((label for fragment, label in shapes if fragment in path), None)
        if name is None or name in written:
            continue
        written[name] = anonymiser.payload(response)

    if written:
        out.mkdir(parents=True, exist_ok=True)
        for name, payload in written.items():
            (out / f"{name}.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + chr(10), encoding="utf-8"
            )
    return list(written)


def _default_out(provider: str) -> Path:
    root = gitctx.repo_root(Path.cwd()) or Path.cwd()
    return root / "tests" / "fixtures" / provider / "local"


def _scan(provider) -> dict:
    """The scan needs a real ticket; use the first one this context can see."""
    try:
        rows = provider.list_tasks(1)
        key = rows[0]["key"] if rows else ""
        return provider.scan_fields(key) if key else {}
    except Exception:  # noqa: BLE001 - --deep is a bonus on top of a passing test
        return {}


def _discarded(provider) -> None:
    """Fields present in the payload that normalisation drops.

    A custom field carrying acceptance criteria is invisible today: it is simply
    not in the normalised task, and nothing says so. Silence there is worse than
    a wrong mapping, because a wrong mapping gets noticed.
    """
    seen = _scan(provider)
    if not seen:
        print("    --deep: nothing unread, or this provider cannot scan")
        return
    print(f"    {len(seen)} field(s) in the payload are not read by this tool:")
    for name, sample in sorted(seen.items())[:20]:
        print(f"      {name}  e.g. {sample}")
    print('    map one:  "field_map": {"<id>": "acceptance_criteria"} in .workflow/config.json')
