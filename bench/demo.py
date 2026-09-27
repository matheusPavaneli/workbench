"""Record the README's demo: wb start, wb next, wb finish and wb approve on BN-6.

    python bench/demo.py [--out docs/demo.cast]

Every line of output in the recording is what the command printed, run for real
against a fresh copy of the benchmark fixture; nothing is typed into the file by
hand. The one edit is the fix itself, made by a command shown on screen, since a
recording of an editor would show nothing wb does. The pace -- typing speed and
the pause after each output -- is set here, so the clip lasts 30-60 s whatever
the machine; the real duration of each command is not what the clip is about.

Writes an asciicast v2 file: `asciinema play docs/demo.cast`, or any asciicast
player.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import run as bench_run

ROOT = bench_run.ROOT
TICKET = "BN-6"
MESSAGE = "fix: allow ordering exactly the units on hand"
FIX = "shop/stock.py"
TEST = "tests/test_stock.py"
TEST_ADDED = (
    "\n\nclass LastUnitTest(unittest.TestCase):\n"
    "    def test_the_last_units_on_hand_can_be_ordered(self):\n"
    "        self.assertTrue(available({\"tea\": 2}, \"tea\", 2))\n"
)
TYPE_S = 0.06
# Time to read an output: a base, plus a little per line.
PAUSE_S = 3.5
PAUSE_PER_LINE_S = 0.45
WIDTH, HEIGHT = 100, 30


def steps(wb: str) -> list[tuple[str, list[str] | None]]:
    """(what is shown at the prompt, the argv that runs it; None for a shell step done in Python)."""
    return [
        (f"wb start {TICKET}", [sys.executable, wb, "start", TICKET]),
        ("wb next", [sys.executable, wb, "next"]),
        (f"sed -i 's/> quantity/>= quantity/' {FIX}   # the fix, and a test for it", None),
        (f'wb finish {TICKET} -m "{MESSAGE}" --commit', [sys.executable, wb, "finish", TICKET, "-m", MESSAGE, "--commit"]),
        ("wb approve", [sys.executable, wb, "approve"]),
    ]


def record(out: Path) -> Path:
    ticket = next(t for t in bench_run.load_tickets([TICKET]))
    with bench_run.workdir("wb-demo-") as work:
        env = bench_run.session_env(work)
        env["WB_NO_HOOKS"] = "1"
        plugin = bench_run.copy_plugin(work / "plugin")
        repo = work / "repo"
        bench_run.prepare(repo, ticket, "workbench", env, plugin)
        wb = str(plugin / "lib" / "wb.py")

        events: list[list] = []
        clock = 0.5
        for shown, argv in steps(wb):
            clock = _type(events, clock, f"$ {shown}")
            if argv is None:
                _fix(repo)
                output = ""
            else:
                done = subprocess.run(argv, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                      text=True, encoding="utf-8", errors="replace", timeout=300)
                output = (done.stdout + done.stderr).replace(str(work), "~").replace(str(work).replace("\\", "/"), "~")
            events.append([round(clock, 3), "o", "\r\n" + output.replace("\n", "\r\n")])
            clock += (PAUSE_S + PAUSE_PER_LINE_S * output.count("\n")) if output else 1.2

        out.parent.mkdir(parents=True, exist_ok=True)
        header = {"version": 2, "width": WIDTH, "height": HEIGHT, "title": f"workbench: {TICKET} on the light path",
                  "env": {"SHELL": "/bin/sh", "TERM": "xterm-256color"}}
        lines = [json.dumps(header)] + [json.dumps(event, ensure_ascii=False) for event in events]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _type(events: list[list], clock: float, text: str) -> float:
    for character in text:
        events.append([round(clock, 3), "o", character])
        clock += TYPE_S
    return clock + 0.4


def _fix(repo: Path) -> None:
    path = repo / FIX
    # newline="\n": the fixture's line endings, as an editor keeps them. A CRLF
    # rewrite on Windows would read as every line changed.
    fixed = path.read_text(encoding="utf-8").replace("> quantity", ">= quantity")
    path.write_text(fixed, encoding="utf-8", newline="\n")
    test = repo / TEST
    test.write_text(test.read_text(encoding="utf-8").rstrip("\n") + TEST_ADDED, encoding="utf-8", newline="\n")


def duration(cast: Path) -> float:
    last = cast.read_text(encoding="utf-8").strip().splitlines()[-1]
    return float(json.loads(last)[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record the README demo (asciicast v2).")
    parser.add_argument("--out", default=str(ROOT / "docs" / "demo.cast"))
    args = parser.parse_args(argv)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    path = record(Path(args.out))
    print(f"wrote {path} ({duration(path):.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
