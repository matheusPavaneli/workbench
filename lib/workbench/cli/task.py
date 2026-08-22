"""``wb task`` -- read work from the tracker, and tidy up after it.

Deliberately unequal. ``list`` is the most frequent question and the one that
needs the least detail, so it answers in four columns. ``get`` distils one task
and writes the artifact everything downstream reads. ``new`` and ``done`` write
to a local backlog and refuse on a repo whose tracker owns its own state.
``clean`` removes a ticket's artifacts once nobody needs them.

None of them takes a query. There is no --jql, no --wiql, no --fields: the
query is built in the provider, from the resolved context.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from .. import artifacts, contexts, gitctx, providers, status as status_lib
from ..errors import NotFoundError, UsageError, WbError
from ..providers import local

ACTIONS = ["list", "get", "new", "done", "clean"]
DEFAULT_LIST_LIMIT = 20

# Artifacts a skill wrote from a conversation rather than from the tracker.
# Everything else in a ticket directory can be produced again -- `wb task get`,
# a re-plan, a re-verify -- so these are the only lines in the listing that
# describe a decision rather than a chore.
UNRECOVERABLE = ("frame.md", "handover.md")


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("task", help="read tasks from the tracker")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    listing = actions.add_parser("list", help="your open tasks: key, status, title, updated")
    listing.add_argument("--limit", type=int, default=DEFAULT_LIST_LIMIT)

    get = actions.add_parser("get", help="distil one task into .workflow/<KEY>/triage.json")
    get.add_argument("key", help="Jira issue key (ABC-123) or Azure work item id")
    get.add_argument(
        "--depth",
        type=int,
        default=1,
        choices=[0, 1, 2],
        help="0 task only; 1 linked items one line each; 2 bodies of blocking and hierarchy links",
    )
    get.add_argument(
        "--expand",
        action="append",
        default=[],
        metavar="HANDLE",
        help="a handle taken verbatim from a previous run's _expand list; repeatable",
    )
    get.add_argument("--no-cache", action="store_true", help="ignore the 15 minute cache")

    new = actions.add_parser("new", help="record a task in the local backlog (local contexts only)")
    new.add_argument("title", help="what needs doing, in one line")
    new.add_argument("--type", dest="kind", default="feature", choices=local.TYPES)
    new.add_argument("--desc", default="", help="the detail: acceptance criteria, a reproduction, constraints")
    new.add_argument("--key", help="an explicit key; defaults to the next free WB-<n>")
    new.add_argument("--link", action="append", default=[], metavar="KEY", help="a related task; repeatable")

    done = actions.add_parser("done", help="close a task in the local backlog, or move it to another status")
    done.add_argument("key")
    done.add_argument(
        "--status",
        default=local.DONE,
        choices=local.STATUSES,
        help="the status to move to; defaults to done",
    )

    clean = actions.add_parser("clean", help="remove finished tickets' .workflow/<KEY>/ artifacts")
    clean.add_argument("key", nargs="?", help="one ticket; omit it and name a selector instead")
    clean.add_argument(
        "--merged",
        action="store_true",
        help="every ticket that reached a commit or a PR and has no branch left on the remote",
    )
    clean.add_argument(
        "--older-than",
        dest="older_than",
        metavar="Nd",
        help="every ticket untouched for longer than this, e.g. 30d",
    )
    clean.add_argument(
        "--force",
        action="store_true",
        help="actually remove them; without this the files are only listed",
    )


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb task needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"list": _list, "get": _get, "new": _new, "done": _done, "clean": _clean}[args.action](args)


def _list(args: argparse.Namespace) -> int:
    if args.limit < 1 or args.limit > 100:
        raise UsageError(f"--limit {args.limit} is out of range", fix=["use 1..100"])

    provider = providers.for_context(contexts.resolve().context)
    rows = provider.list_tasks(args.limit)
    if not rows:
        print("no open tasks assigned to you")
        return 0

    width = max(len(row["key"]) for row in rows)
    for row in rows:
        print(f"{row['key']:<{width}}  {row['updated']}  {row['status']:<14}  {row['title']}")
    return 0


def _new(args: argparse.Namespace) -> int:
    _require_local("create the task")
    data = local.create(
        args.title, kind=args.kind, desc=args.desc, key=args.key, links=args.link
    )
    print(f"{data['key']}  {data['type']:<8} {data['title']}")
    print(f"wrote {local.task_path(data['key'])}")
    print(f"next: wb task get {data['key']}")
    return 0


def _done(args: argparse.Namespace) -> int:
    _require_local("close a task")
    previous, data = local.set_status(args.key, args.status)
    if previous == data["status"]:
        print(f"{data['key']}  already {data['status']}")
        return 0
    print(f"{data['key']}  {previous} -> {data['status']}  {data['title']}")
    return 0


def _clean(args: argparse.Namespace) -> int:
    """Remove finished tickets' scratch, and nothing that is a sibling of it.

    The guard is structural rather than a list of names to avoid. Every path
    here is resolved through ``artifacts.ticket_dir``, which routes the key
    through ``validate_key`` -- and neither the key pattern nor the slug pattern
    can produce ``tasks``, ``config.json`` or ``.events.jsonl``. Those three sit
    beside the ticket directories under the same root, and two of them are
    committed, so a blacklist that drifted would be the whole risk of the
    command. There is nothing to drift.

    Listing is the default because there is no undo: a plan and its evidence can
    be produced again, but a frame or a handover was written once, by hand. The
    listing also says which stage each ticket stopped at, because a selector
    matching work still in flight is the mistake worth catching before --force.
    """
    keys = _selected(args)
    if not keys:
        print(_nothing_matched(args))
        return 0

    if not args.force:
        return _list_what_would_go(keys, args)

    removed = 0
    for key in keys:
        directory = artifacts.ticket_dir(key)
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            raise WbError(
                f"could not remove {directory}: {exc}",
                fix=["close anything holding a file open in that directory, then run it again"],
            ) from exc
        print(f"removed {directory}")
        removed += 1

    print(f"\n{removed} ticket(s) removed")
    return 0


def _selected(args: argparse.Namespace) -> list[str]:
    """The tickets this invocation names, by key or by selector.

    One at a time: a key answers for itself, and mixing it with a selector reads
    as "this one as well", which is not what either does.
    """
    chosen = [name for name, given in (("a key", args.key), ("--merged", args.merged),
                                       ("--older-than", args.older_than)) if given]
    if len(chosen) > 1:
        raise UsageError(
            f"{' and '.join(chosen)} select different things",
            fix=["name one ticket, or use one selector"],
        )
    if not chosen:
        raise UsageError(
            "wb task clean needs a ticket or a selector",
            fix=["wb task clean ABC-123", "wb task clean --merged", "wb task clean --older-than 30d"],
        )

    if args.key:
        directory = artifacts.ticket_dir(args.key)
        if not directory.is_dir():
            raise NotFoundError(
                f"{directory} does not exist",
                fix=["wb status   # the keys that do have artifacts in this checkout"],
            )
        return [artifacts.validate_key(args.key)]

    if args.older_than:
        return _stale(_days(args.older_than))
    return _merged()


def _days(raw: str) -> int:
    text = raw.strip().lower()
    text = text[:-1] if text.endswith("d") else text
    if not text.isdigit() or int(text) < 1:
        raise UsageError(f"--older-than {raw!r} is not a number of days", fix=["use a whole number, e.g. 30d"])
    return int(text)


def _stale(days: int) -> list[str]:
    """Tickets untouched for longer than ``days``, newest file in each one.

    The directory's own timestamp is not enough: editing a plan in place leaves
    it alone, so a ticket worked on yesterday could read as months old.
    """
    cutoff = time.time() - days * 86400
    return [key for key in status_lib.keys() if _touched(artifacts.ticket_dir(key)) < cutoff]


def _touched(directory: Path) -> float:
    stamps = [directory.stat().st_mtime]
    stamps.extend(path.stat().st_mtime for path in directory.rglob("*") if path.is_file())
    return max(stamps)


def _merged() -> list[str]:
    """Tickets whose work shipped and whose branch is no longer on the remote.

    Both halves are needed. "No branch names this key" alone also matches a
    ticket that was triaged and never branched at all -- which is work in
    flight, not work finished, and deleting it would be exactly backwards. A
    ``commit.txt`` or a ``pr.md`` is the evidence that a branch once existed.
    """
    root = gitctx.repo_root(Path.cwd()) or Path.cwd()
    branches = gitctx.remote_branches(root)

    selected = []
    for key in status_lib.keys():
        directory = artifacts.ticket_dir(key)
        if not (directory / "commit.txt").is_file() and not (directory / "pr.md").is_file():
            continue
        if any(status_lib._spelled_in(key, name) for name in branches):
            continue
        selected.append(key)
    return selected


def _list_what_would_go(keys: list[str], args: argparse.Namespace) -> int:
    total = 0
    for key in keys:
        directory = artifacts.ticket_dir(key)
        files = sorted(path for path in directory.rglob("*") if path.is_file())
        total += len(files)

        print(f"{directory}{_unfinished(key)}")
        for path in files:
            name = path.relative_to(directory).as_posix()
            note = "   hand-written, not regenerable" if name in UNRECOVERABLE else ""
            print(f"  {name}{note}")

    print(f"\n{len(keys)} ticket(s), {total} file(s), nothing removed")
    print(f"remove them: {_invocation(args)} --force")
    return 0


def _unfinished(key: str) -> str:
    """What the pipeline still expects, said next to the directory about to go.

    A selector is a guess about which work is over. This is the sentence that
    lets somebody notice the guess was wrong while it is still only a listing.
    """
    try:
        status = status_lib.read(key)
    except Exception:  # noqa: BLE001 - a listing must not fail on one unreadable ticket
        return ""
    stage = status.blocked or status.next_stage
    if stage is None:
        return ""
    return f"   still at {stage.name}" + (" (BLOCKED)" if status.blocked else "")


def _invocation(args: argparse.Namespace) -> str:
    if args.key:
        return f"wb task clean {args.key}"
    if args.older_than:
        return f"wb task clean --older-than {args.older_than}"
    return "wb task clean --merged"


def _nothing_matched(args: argparse.Namespace) -> str:
    if args.older_than:
        return f"no ticket has been untouched for {args.older_than}"
    return "no ticket has shipped and lost its branch"


def _require_local(what: str) -> None:
    """A tracker owns its own state; writing it here would only lie locally."""
    context = contexts.resolve().context
    if context.provider != local.LocalProvider.name:
        raise UsageError(
            f"this repo resolves to a {context.provider} context, which owns its own tasks",
            fix=[
                f"{what} in {context.provider} itself",
                "or bind this repo to a local backlog: wb ctx use <local-context>",
            ],
        )


def _get(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    provider = providers.for_context(contexts.resolve().context)
    payload = provider.get_task(key, args.depth, args.expand, use_cache=not args.no_cache)

    path = artifacts.write_json(key, "triage.json", payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nwrote {path}", flush=True)
    return 0
