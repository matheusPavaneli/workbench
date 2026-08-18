#!/usr/bin/env python3
"""wb -- the one command the skills call.

Invoked as:

    python "${CLAUDE_PLUGIN_ROOT}/lib/wb.py" <group> <action> [flags]

The surface is deliberately closed. There is no free-text query flag -- no
--jql, no --wiql, no --fields, no --url -- because a free-text flag is an
invitation to invent one. An unknown group, action or flag exits 2 and lists
what is valid; it never guesses.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from workbench import events  # noqa: E402
from workbench import redact  # noqa: E402
from workbench.cli import ctx as ctx_cli  # noqa: E402
from workbench.cli import commit as commit_cli  # noqa: E402
from workbench.cli import doctor as doctor_cli  # noqa: E402
from workbench.cli import flow as flow_cli  # noqa: E402
from workbench.cli import git as git_cli  # noqa: E402
from workbench.cli import impl as impl_cli  # noqa: E402
from workbench.cli import next as next_cli  # noqa: E402
from workbench.cli import pr as pr_cli  # noqa: E402
from workbench.cli import repo as repo_cli  # noqa: E402
from workbench.cli import review as review_cli  # noqa: E402
from workbench.cli import sdd as sdd_cli  # noqa: E402
from workbench.cli import status as status_cli  # noqa: E402
from workbench.cli import task as task_cli  # noqa: E402
from workbench.errors import EXIT_USAGE, UsageError, WbError  # noqa: E402

GROUPS = {
    "ctx": ctx_cli,
    "doctor": doctor_cli,
    "status": status_cli,
    "next": next_cli,
    "task": task_cli,
    "repo": repo_cli,
    "sdd": sdd_cli,
    "flow": flow_cli,
    "impl": impl_cli,
    "review": review_cli,
    "commit": commit_cli,
    "pr": pr_cli,
    "git": git_cli,
}


class _StrictParser(argparse.ArgumentParser):
    """argparse's default error path prints usage and exits 2 silently.

    Route it through our error format instead, so the caller always gets the
    same shape: one ``error:`` line, then concrete ``fix:`` lines.
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        usage = " ".join(self.format_usage().split())
        raise UsageError(message, fix=[usage])


class _ScrubbedStream(io.TextIOBase):
    """Last line of defence: nothing reaches a terminal unscrubbed."""

    def __init__(self, stream: io.TextIOBase) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        self._stream.write(redact.scrub(text))
        return len(text)

    def flush(self) -> None:
        self._stream.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = _StrictParser(prog="wb", description="ticket-to-PR workflow tooling", allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="group", metavar="{" + ",".join(GROUPS) + "}")
    for module in GROUPS.values():
        module.register(subparsers)
    return parser


def _timed(args: argparse.Namespace) -> int:
    """Run the command and record what happened, without ever changing it.

    A raised WbError is logged with its own exit code and re-raised, so a
    failure counts in the history rather than only a success.
    """
    started = time.monotonic()
    code = 0
    try:
        code = GROUPS[args.group].run(args)
        return code
    except WbError as exc:
        code = exc.code
        raise
    except BaseException:
        code = EXIT_USAGE
        raise
    finally:
        events.record(
            args.group,
            getattr(args, "action", "") or "",
            getattr(args, "key", None),
            code,
            int((time.monotonic() - started) * 1000),
        )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv if argv is not None else sys.argv[1:])
        if not args.group:
            raise UsageError("wb needs a command group", fix=[f"groups: {', '.join(sorted(GROUPS))}"])
        return _timed(args)

    except WbError as exc:
        print(redact.scrub(exc.render()), file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - never let a raw traceback carry a token out
        print(redact.scrub(f"error: unexpected failure: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.stdout = _ScrubbedStream(sys.stdout)  # type: ignore[assignment]
    sys.stderr = _ScrubbedStream(sys.stderr)  # type: ignore[assignment]
    raise SystemExit(main())
