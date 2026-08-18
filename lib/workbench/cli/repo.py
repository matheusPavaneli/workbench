"""``wb repo`` -- what bar this repo is held to, and where it rises.

The gates are emitted as resolved lines rather than described in a document the
agent has to read and apply. A table of five presets costs the same tokens every
session and invites the model to pick the wrong row; six lines of applicable
rules do not.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import gitctx, profile as profile_lib
from ..errors import UsageError

ACTIONS = ["profile", "zones", "gates"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("repo", help="quality preset and critical zones")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    detect = actions.add_parser("profile", help="detect the preset and print the gates that apply")
    detect.add_argument("--set", dest="preset", choices=profile_lib.PRESETS, help="override the detected preset")
    detect.add_argument("--confirm", action="store_true", help="accept the detected preset as reviewed")
    detect.add_argument("--json", action="store_true", help="machine-readable output")

    zones = actions.add_parser("zones", help="which critical zones a set of paths touches")
    zones.add_argument("paths", nargs="+")

    gates = actions.add_parser("gates", help="the gates that apply to a specific set of paths")
    gates.add_argument("paths", nargs="+")
    gates.add_argument("--json", action="store_true", help="machine-readable output")


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb repo needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"profile": _profile, "zones": _zones, "gates": _gates}[args.action](args)


def _profile(args: argparse.Namespace) -> int:
    root = gitctx.repo_root(Path.cwd()) or Path.cwd()

    if args.preset or args.confirm:
        chosen = args.preset or profile_lib.resolve(root).preset
        print(f"wrote preset {chosen} to {profile_lib.record(root, chosen)}")

    resolved = profile_lib.resolve(root)

    if args.json:
        print(json.dumps({**resolved.to_dict(), "gates": resolved.gates()}, indent=2))
        return 0

    origin = "override" if resolved.preset != resolved.detected else "detected"
    if resolved.confirmed:
        origin += ", confirmed"
    elif resolved.confidence == profile_lib.LOW:
        origin += ", LOW confidence"
    print(f"preset    {resolved.preset}  ({origin} from: {', '.join(resolved.signals)})")
    if resolved.conventions:
        print("repo      " + "  ".join(f"{k}={v}" for k, v in sorted(resolved.conventions.items())))

    by_path = profile_lib.preset_paths(root)
    if by_path:
        print("by path   " + "  ".join(f"{rule}={preset}" for rule, preset in sorted(by_path.items())))

    print("gates:")
    for gate in resolved.gates():
        print(f"  - {gate}")

    if resolved.needs_confirmation:
        # The point of confidence: an unreviewed guess asks once, then stops.
        print(f"\nthe evidence supports more than one bar; also plausible: {', '.join(resolved.alternatives)}")
        if "monorepo" in resolved.signals:
            print("a monorepo holds several products to one bar unless preset_paths says otherwise:")
            print('  .workflow/config.json -> "preset_paths": {"packages/billing/**": "enterprise"}')
        print("confirm it:    wb repo profile --confirm")
        print(f"or change it:  wb repo profile --set {{{','.join(profile_lib.PRESETS)}}}")
    elif resolved.preset == resolved.detected and not resolved.confirmed:
        print(f"\noverride with: wb repo profile --set {{{','.join(profile_lib.PRESETS)}}}")
    return 0


def _gates(args: argparse.Namespace) -> int:
    """Resolved for the files a plan touches, not for the repo as a whole."""
    root = gitctx.repo_root(Path.cwd()) or Path.cwd()
    resolved = profile_lib.resolve(root)
    preset, hits = profile_lib.resolve_for(args.paths, profile_lib.preset_paths(root), resolved.preset)
    gates = profile_lib.gates_for(preset, args.paths)

    if args.json:
        print(json.dumps({"preset": preset, "by_preset": hits, "gates": gates}, indent=2))
        return 0

    spans = "  (the highest of the presets these paths land in)" if len(hits) > 1 else ""
    print(f"preset    {preset}{spans}")
    for name, matched in sorted(hits.items(), key=lambda kv: profile_lib.RANK.get(kv[0], 0), reverse=True):
        print(f"  {name:<11} {', '.join(sorted(matched))}")
    print("gates:")
    for gate in gates:
        print(f"  - {gate}")
    if resolved.needs_confirmation:
        print("\npreset unconfirmed: wb repo profile --confirm")
    return 0


def _zones(args: argparse.Namespace) -> int:
    hits = profile_lib.critical_zones(args.paths)
    if not hits:
        print("no critical zones touched")
        return 0
    print("critical zones touched -- the bar rises here regardless of preset:")
    for zone, paths in sorted(hits.items()):
        print(f"  {zone}: {', '.join(sorted(set(paths)))}")
    return 0
