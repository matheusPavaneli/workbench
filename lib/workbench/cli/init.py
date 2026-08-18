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
    root = gitctx.repo_root(Path.cwd())
    if root is None:
        raise UsageError("not a git repository", fix=["run this inside a checkout, or: git init"])

    path = root / contexts.REPO_CONFIG
    existing = profile_lib.repo_config(root)
    if existing and not args.force and args.write:
        raise UsageError(
            f"{path} already exists",
            fix=["review it first: wb doctor", "replace it deliberately: wb init --write --force"],
        )

    proposal, notes = _propose(root, args, existing)

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
    print("check the whole chain:  wb doctor")
    return 0


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
            notes.append(f"flow      source {flow.source.branch}, validation {targets} -- read off the remote branches")

    if provider != "local":
        notes.append("")
        notes.append(f"a {provider} context still needs a credential; this command never writes one:")
        notes.append(f"  wb ctx add <name> --provider {provider} ...   then   wb ctx use <name>")

    return proposal, notes


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
