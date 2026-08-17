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

from .. import contexts, gitctx, profile as profile_lib
from ..errors import UsageError

ACTIONS = ["profile", "zones"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("repo", help="quality preset and critical zones")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    detect = actions.add_parser("profile", help="detect the preset and print the gates that apply")
    detect.add_argument("--set", dest="preset", choices=profile_lib.PRESETS, help="override the detected preset")
    detect.add_argument("--json", action="store_true", help="machine-readable output")

    zones = actions.add_parser("zones", help="which critical zones a set of paths touches")
    zones.add_argument("paths", nargs="+")


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb repo needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"profile": _profile, "zones": _zones}[args.action](args)


def _profile(args: argparse.Namespace) -> int:
    root = gitctx.repo_root(Path.cwd()) or Path.cwd()
    detected = profile_lib.detect(root)

    chosen = args.preset or _stored_preset(root) or detected.preset
    detected.preset = chosen

    if args.preset:
        _store_preset(root, args.preset)

    if args.json:
        print(json.dumps({**detected.to_dict(), "gates": detected.gates()}, indent=2))
        return 0

    origin = "override" if chosen != detected.detected else "detected"
    print(f"preset    {chosen}  ({origin} from: {', '.join(detected.signals)})")
    if detected.conventions:
        print("repo      " + "  ".join(f"{k}={v}" for k, v in sorted(detected.conventions.items())))
    print("gates:")
    for gate in detected.gates():
        print(f"  - {gate}")
    if chosen == detected.detected:
        print(f"\noverride with: wb repo profile --set {{{','.join(profile_lib.PRESETS)}}}")
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


def _stored_preset(root: Path) -> str | None:
    path = root / contexts.REPO_CONFIG
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    preset = data.get("preset")
    return str(preset) if preset in profile_lib.PRESETS else None


def _store_preset(root: Path, preset: str) -> None:
    """Merge into the repo config; the context binding must survive."""
    path = root / contexts.REPO_CONFIG
    data = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    data["preset"] = preset
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote preset {preset} to {path}")
