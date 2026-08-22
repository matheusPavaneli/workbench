"""``wb task`` -- read work from the tracker.

Two commands, deliberately unequal. ``list`` is the most frequent question and
the one that needs the least detail, so it answers in four columns. ``get``
distils one task and writes the artifact everything downstream reads.

Neither takes a query. There is no --jql, no --wiql, no --fields: the query is
built in the provider, from the resolved context.
"""

from __future__ import annotations

import argparse
import json
import shutil

from .. import artifacts, contexts, providers
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

    clean = actions.add_parser("clean", help="remove one ticket's .workflow/<KEY>/ artifacts")
    clean.add_argument("key")
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
    """Remove one ticket's scratch, and nothing that is a sibling of it.

    The guard is structural rather than a list of names to avoid. Every path
    here is resolved through ``artifacts.ticket_dir``, which routes the key
    through ``validate_key`` -- and neither the key pattern nor the slug pattern
    can produce ``tasks``, ``config.json`` or ``.events.jsonl``. Those three sit
    beside the ticket directories under the same root, and two of them are
    committed, so a blacklist that drifted would be the whole risk of the
    command. There is nothing to drift.

    Listing is the default because there is no undo: a plan and its evidence can
    be produced again, but a frame or a handover was written once, by hand.
    """
    directory = artifacts.ticket_dir(args.key)
    if not directory.is_dir():
        raise NotFoundError(
            f"{directory} does not exist",
            fix=["wb status   # the keys that do have artifacts in this checkout"],
        )

    files = sorted(path for path in directory.rglob("*") if path.is_file())

    if not args.force:
        print(directory)
        for path in files:
            name = path.relative_to(directory).as_posix()
            note = "   hand-written, not regenerable" if name in UNRECOVERABLE else ""
            print(f"  {name}{note}")
        print(f"\n{len(files)} file(s), nothing removed")
        print(f"remove them: wb task clean {args.key} --force")
        return 0

    try:
        shutil.rmtree(directory)
    except OSError as exc:
        raise WbError(
            f"could not remove {directory}: {exc}",
            fix=["close anything holding a file open in that directory, then run it again"],
        ) from exc

    print(f"removed {directory}  ({len(files)} file(s))")
    return 0


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
