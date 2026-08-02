#!/usr/bin/env python3
"""Fail if handoff.md / SPEC.md quote a test count that does not match the suite.

Default mode runs the full suite (`pytest -q`) and requires docs to match the
runtime summary exactly — both passed and skipped. That way a silent skip
increase (e.g. missing ffmpeg turning a test into a skip) fails CI instead of
looking green.

Also available:
  --collect-only     docs vs pytest --collect-only sum (no suite run)
  --pytest-output F  parse an existing pytest -q log instead of re-running

The orientation line in handoff.md and the status blurb in SPEC.md are what
readers use to judge whether the docs are current. That number has drifted by
hand twice already; this script makes the mismatch a CI failure.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Last non-empty pytest -q summary line, e.g.:
#   529 passed, 1 skipped in 12.34s
#   1 failed, 528 passed, 1 skipped in 12.34s
#   529 passed in 12.34s
_SUMMARY_RE = re.compile(
    r"(?:(\d+)\s+failed,\s*)?"
    r"(\d+)\s+passed"
    r"(?:,\s*(\d+)\s+skipped)?"
    r"(?:,\s*\d+\s+\w+)*"  # xfailed, warnings, etc.
    r"\s+in\s+"
)


def collected_count() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    match = re.search(r"(\d+)\s+tests?\s+collected", text)
    if not match:
        sys.stderr.write(
            "could not parse pytest --collect-only output:\n"
            f"exit={proc.returncode}\n{text}\n"
        )
        raise SystemExit(2)
    if proc.returncode != 0:
        sys.stderr.write(
            f"pytest --collect-only failed (exit {proc.returncode}):\n{text}\n"
        )
        raise SystemExit(2)
    return int(match.group(1))


def parse_pytest_summary(text: str) -> tuple[int, int, int]:
    """Return (passed, skipped, failed) from pytest -q terminal output."""
    # Walk lines from the end so we hit the final summary, not progress noise.
    for line in reversed(text.splitlines()):
        match = _SUMMARY_RE.search(line)
        if match:
            failed = int(match.group(1) or 0)
            passed = int(match.group(2))
            skipped = int(match.group(3) or 0)
            return passed, skipped, failed
    sys.stderr.write(
        "could not parse pytest summary line (expected "
        "'N passed[, M skipped] in …'):\n"
        f"{text[-2000:]}\n"
    )
    raise SystemExit(2)


def run_pytest() -> tuple[int, int, int, int]:
    """Run pytest -q; stream output; return (passed, skipped, failed, exit)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    out = proc.stdout or ""
    err = proc.stderr or ""
    # Stream suite output so CI logs show failures, not just the docs check.
    if out:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    if err:
        sys.stderr.write(err)
        if not err.endswith("\n"):
            sys.stderr.write("\n")
    passed, skipped, failed = parse_pytest_summary(out + err)
    return passed, skipped, failed, proc.returncode


def parse_handoff(text: str) -> tuple[int, int]:
    # Orientation: `uv run pytest -q` → **526 passed, 1 skipped**
    match = re.search(
        r"pytest[^\n]*\*\*(\d+)\s+passed(?:,\s*(\d+)\s+skipped)?\*\*",
        text,
    )
    if not match:
        match = re.search(
            r"\*\*(\d+)\s+passed(?:,\s*(\d+)\s+skipped)?\*\*",
            text,
        )
    if not match:
        sys.stderr.write(
            "handoff.md: no '**N passed[, M skipped]**' quote found near pytest\n"
        )
        raise SystemExit(1)
    return int(match.group(1)), int(match.group(2) or 0)


def parse_spec(text: str) -> tuple[int, int]:
    # Status blurb: Test suite: 526 passing, 1 skipped.
    match = re.search(
        r"Test suite:\s*(\d+)\s+pass(?:ed|ing)(?:,\s*(\d+)\s+skipped)?",
        text,
    )
    if not match:
        sys.stderr.write(
            "SPEC.md: no 'Test suite: N passing[, M skipped]' quote found\n"
        )
        raise SystemExit(1)
    return int(match.group(1)), int(match.group(2) or 0)


def _load_docs() -> tuple[tuple[int, int], tuple[int, int]]:
    handoff = (ROOT / "handoff.md").read_text(encoding="utf-8")
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    return parse_handoff(handoff), parse_spec(spec)


def _check_docs_agree(
    h: tuple[int, int], s: tuple[int, int], errors: list[str]
) -> None:
    h_passed, h_skipped = h
    s_passed, s_skipped = s
    if (h_passed, h_skipped) != (s_passed, s_skipped):
        errors.append(
            f"handoff.md ({h_passed} passed, {h_skipped} skipped) disagrees with "
            f"SPEC.md ({s_passed} passing, {s_skipped} skipped)"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--collect-only",
        action="store_true",
        help="only compare docs to pytest --collect-only sum (do not run suite)",
    )
    mode.add_argument(
        "--pytest-output",
        metavar="FILE",
        help="parse this pytest -q log instead of running the suite",
    )
    args = parser.parse_args(argv)

    h, s = _load_docs()
    h_passed, h_skipped = h
    s_passed, s_skipped = s
    errors: list[str] = []
    _check_docs_agree(h, s, errors)

    if args.collect_only:
        collected = collected_count()
        if h_passed + h_skipped != collected:
            errors.append(
                f"handoff.md quotes {h_passed} passed, {h_skipped} skipped "
                f"(sum {h_passed + h_skipped}) but suite collects {collected}"
            )
        if s_passed + s_skipped != collected:
            errors.append(
                f"SPEC.md quotes {s_passed} passing, {s_skipped} skipped "
                f"(sum {s_passed + s_skipped}) but suite collects {collected}"
            )
        if errors:
            for err in errors:
                print(err, file=sys.stderr)
            return 1
        print(
            f"ok: docs match suite — {h_passed} passed, {h_skipped} skipped "
            f"({collected} collected)"
        )
        return 0

    if args.pytest_output:
        text = Path(args.pytest_output).read_text(encoding="utf-8")
        passed, skipped, failed = parse_pytest_summary(text)
        suite_exit = 1 if failed else 0
    else:
        passed, skipped, failed, suite_exit = run_pytest()

    # Exact match on runtime passed and skipped (not merely sum).
    if (passed, skipped) != (h_passed, h_skipped):
        errors.append(
            f"handoff.md quotes {h_passed} passed, {h_skipped} skipped but "
            f"suite ran {passed} passed, {skipped} skipped"
        )
    if (passed, skipped) != (s_passed, s_skipped):
        errors.append(
            f"SPEC.md quotes {s_passed} passing, {s_skipped} skipped but "
            f"suite ran {passed} passed, {skipped} skipped"
        )
    if failed:
        errors.append(f"suite reported {failed} failed test(s)")
    if suite_exit != 0 and not failed:
        # Non-zero without a parseable failed count (collection error, etc.).
        errors.append(f"pytest exited {suite_exit}")

    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1

    print(
        f"ok: docs match suite — {passed} passed, {skipped} skipped "
        f"(runtime)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
