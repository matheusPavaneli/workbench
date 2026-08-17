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

from .. import artifacts, contexts, providers
from ..errors import UsageError
from ..providers import local

ACTIONS = ["list", "get", "new"]
DEFAULT_LIST_LIMIT = 20


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


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb task needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"list": _list, "get": _get, "new": _new}[args.action](args)


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
    context = contexts.resolve().context
    if context.provider != local.LocalProvider.name:
        raise UsageError(
            f"this repo resolves to a {context.provider} context, which owns its own tasks",
            fix=[
                f"create the task in {context.provider} and run: wb task get <KEY>",
                "or bind this repo to a local backlog: wb ctx use <local-context>",
            ],
        )

    data = local.create(
        args.title, kind=args.kind, desc=args.desc, key=args.key, links=args.link
    )
    print(f"{data['key']}  {data['type']:<8} {data['title']}")
    print(f"wrote {local.task_path(data['key'])}")
    print(f"next: wb task get {data['key']}")
    return 0


def _get(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    provider = providers.for_context(contexts.resolve().context)
    payload = provider.get_task(key, args.depth, args.expand, use_cache=not args.no_cache)

    path = artifacts.write_json(key, "triage.json", payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nwrote {path}", flush=True)
    return 0
