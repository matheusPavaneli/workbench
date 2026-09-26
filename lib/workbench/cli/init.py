"""``wb init`` -- from a fresh clone to a working setup, in one pass.

Everything this writes could already be assembled by hand: a provider from the
remote, a preset from `wb repo profile`, a flow from `wb flow show`, a
`.workflow/config.json` holding the three. That was the problem. The first
contact with this tool was a choice between `ctx add` with seven flags and a
JSON file whose keys you had to know already -- both of which require having
understood the model before getting any value out of it.

So this proposes, and the user disposes. It never invents a credential, never
overwrites a decision already recorded, and prints the file it is about to write
before writing it. Detection that cannot be trusted is reported as untrusted,
which is the same rule the preset already follows: a guess that reads like a
finding is worse than no guess.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .. import contexts, flow as flow_lib, gitctx, profile as profile_lib
from ..errors import UsageError

ACTIONS: list[str] = []


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("init", help="set this repo up: provider, preset, flow, in one pass")
    parser.add_argument("--provider", choices=contexts.PROVIDERS, help="skip detection and use this")
    parser.add_argument("--preset", choices=profile_lib.PRESETS, help="skip detection and use this")
    parser.add_argument("--write", action="store_true", help="write the config instead of only proposing it")
    parser.add_argument("--force", action="store_true", help="replace a config that already exists")


def run(args: argparse.Namespace) -> int:
    root = gitctx.require_checkout(fix=["run this inside a checkout, or: git init"])

    path = root / contexts.REPO_CONFIG
    existing = profile_lib.repo_config(root)
    if existing and not args.force and args.write:
        raise UsageError(
            f"{path} already exists",
            fix=["review it first: wb doctor", "replace it deliberately: wb init --write --force"],
        )

    proposal, notes = _propose(root, args, existing)
    ignore = _missing_ignores(root)
    if ignore is None:
        notes.append("gitignore could not ask git whether .workflow/ is ignored; check it with: wb doctor")
    elif ignore:
        notes.append(f"gitignore {'adding' if args.write else 'would add'} to .gitignore: {'  '.join(ignore)}")

    print(f"{'writing' if args.write else 'proposed'}  {path}")
    print()
    for line in json.dumps(proposal, indent=2).splitlines():
        print(f"  {line}")
    print()
    for note in notes:
        print(note)

    if not args.write:
        print()
        print("nothing written. To apply it:  wb init --write")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    print()
    print(f"wrote {path}")
    if ignore:
        _append_ignores(root, ignore)
        print(f"added {len(ignore)} line(s) to {root / '.gitignore'}")
    print("check the whole chain:  wb doctor")
    return 0


# What doctor asks for, and what this repo commits: artifacts are per-checkout
# scratch, except the config and a local backlog, which are shared.
IGNORE_BLOCK = [".workflow/*", "!.workflow/config.json", "!.workflow/tasks/"]
IGNORE_COMMENT = "# workbench: per-checkout scratch; the config and a local backlog are shared"


def _missing_ignores(root: Path) -> list[str] | None:
    """The lines of the standard block .gitignore lacks, if .workflow/ is not
    ignored at all. ``None`` when git could not say.

    Asked of git rather than read off the file, the way doctor asks it: a
    global excludes file or a parent .gitignore may already cover it.
    """
    ignored = gitctx.is_ignored(root, ".workflow/scratch")
    if ignored is None:
        return None
    if ignored:
        return []
    try:
        present = {line.strip() for line in (root / ".gitignore").read_text(encoding="utf-8").splitlines()}
    except OSError:
        present = set()
    return [line for line in IGNORE_BLOCK if line not in present]


def _append_ignores(root: Path, lines: list[str]) -> None:
    """Append, never rewrite: every line already there is somebody's decision."""
    target = root / ".gitignore"
    try:
        current = target.read_text(encoding="utf-8")
    except OSError:
        current = ""
    lead = "" if not current or current.endswith("\n") else "\n"
    gap = "\n" if current else ""
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(lead + gap + "\n".join([IGNORE_COMMENT, *lines]) + "\n")


def _propose(root: Path, args: argparse.Namespace, existing: dict) -> tuple[dict, list[str]]:
    """A config, and the honest notes about how much of it is a guess."""
    notes: list[str] = []
    proposal: dict = dict(existing)  # never drop a decision somebody already made

    provider = args.provider or existing.get("provider") or _detect_provider(root)
    proposal["provider"] = provider
    if provider == "local":
        notes.append("provider  local: a backlog in the repo, no tracker, no credential")
        notes.append("          switch later with: wb ctx use <name>")
    elif not args.provider and not existing.get("provider"):
        notes.append(f"provider  {provider}, from the git remote")

    if provider == "local" and "key_prefix" not in existing and not _has_backlog(root):
        lead = _prefix_for(root.name)
        proposal["key_prefix"] = lead
        notes.append(f"keys      {lead}-1, {lead}-2, ... from the directory name; change key_prefix before the first task")

    profile = profile_lib.resolve(root)
    preset = args.preset or existing.get("preset") or profile.preset
    proposal["preset"] = preset
    proposal["preset_confirmed"] = bool(args.preset) or bool(existing.get("preset_confirmed"))

    if not proposal["preset_confirmed"]:
        confidence = "LOW" if profile.confidence == profile_lib.LOW else "high"
        notes.append(f"preset    {preset}, detected with {confidence} confidence from: {', '.join(profile.signals)}")
        if profile.needs_confirmation:
            notes.append(f"          the evidence also supports: {', '.join(profile.alternatives)}")
            notes.append("          settle it:  wb repo profile --confirm   (or --set <preset>)")

    if not existing.get("flow"):
        flow = flow_lib.resolve(root)
        proposal["flow"] = {
            "source": flow.source.branch,
            "validation": [target.branch for target in flow.validation],
            "strategy": flow.strategy,
            "branch_pattern": flow.pattern,
        }
        if flow.detected:
            targets = ", ".join(t.branch for t in flow.validation) or "none"
            where = "read off the remote branches" if gitctx.has_origin(root) else (
                "read off the local branch; there is no remote yet"
            )
            notes.append(f"flow      source {flow.source.branch}, validation {targets} -- {where}")

    if provider != "local":
        notes.append("")
        notes.append(f"a {provider} context still needs a credential; this command never writes one:")
        notes.append(f"  wb ctx add <name> --provider {provider} ...   then   wb ctx use <name>")

    return proposal, notes


def _has_backlog(root: Path) -> bool:
    """A backlog that already numbers its tasks keeps its prefix: changing it
    would start a second sequence beside the first."""
    tasks = root / ".workflow" / "tasks"
    return tasks.is_dir() and any(tasks.glob("*.json"))


def _prefix_for(name: str) -> str:
    """A key prefix from a directory name: initials of a multi-word name, else
    its first four letters or digits. ``WB`` when neither makes a valid one."""
    words = [word for word in re.split(r"[^A-Za-z0-9]+", name) if word]
    if len(words) > 1:
        candidate = "".join(word[0] for word in words)[:5]
    else:
        candidate = (words[0] if words else "")[:4]
    candidate = candidate.upper()
    if re.match(r"^[A-Z][A-Z0-9]{1,9}$", candidate):
        return candidate
    return "WB"


def _detect_provider(root: Path) -> str:
    """From the remote, or `local` -- which is a real answer, not a fallback.

    Nine of the ten skills never touch a tracker, so a repo with no tracker is a
    supported setup rather than an unfinished one.
    """
    remote = gitctx.origin(root)
    host = (remote.host if remote else "").lower()
    if "github" in host:
        return "github"
    if "dev.azure" in host or "visualstudio" in host:
        return "azure"
    return "local"
