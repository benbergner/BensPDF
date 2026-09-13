#!/usr/bin/env python
"""Run what CI runs, before pushing.

Every check here also runs on a GitHub runner, in `ci.yml` on each push and again
in `release.yml` before anything reaches PyPI. Running them locally first is the
difference between finding a problem in half a minute and finding it in a release
that has already published half of itself.

    python scripts/preflight.py                     # the usual pass
    python scripts/preflight.py --python 3.11 3.13  # add the CI matrix
    python scripts/preflight.py --quick             # source checks only

Run it with any Python you have. It does not use the interpreter it was started
with: it keeps its own environment in `.preflight/`, holding `.[dev]` and nothing
else, which is what CI installs. That is the point of running these locally at
all - a development environment with more installed than CI has can pass a check
CI then fails, and one with less fails checks that are fine.

Two of these are worth naming, because a plain `pytest` run cannot catch either.
The suite runs a second time with tesseract hidden, since the runner has no
tesseract and `release.yml` tests before it publishes. And the built wheel is
installed into a clean environment and driven as a client, since a new module
missing from the artifact still passes every test in the checkout.

It builds into a temporary directory and leaves `dist/` alone, so a run cannot
put a half-built artifact where a release would look for one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent

#: Where the check environment lives. Inside the repo, gitignored, and kept
#: between runs: building it is the slow part, and reusing it makes the whole
#: pass fast enough to run before every push, which is the only way it helps.
ENV_DIR = ROOT / ".preflight"

#: What the environment was last built from. Compared, not trusted by age: a
#: changed dependency has to reinstall, and an unchanged one must not, or the
#: bootstrap becomes the reason nobody runs this.
STAMP = ENV_DIR / "installed-from"

#: Formatting is pinned to the oldest supported Python, as in `ci.yml`. Black
#: changes its mind about a few constructs between targets, so a local run
#: without this passes while CI fails.
BLACK_TARGET = "py311"

#: The versions `ci.yml` builds, newest first. Only used to find an interpreter
#: to build the check environment from, when the one in hand is too old.
SUPPORTED = ("3.13", "3.12", "3.11")

MINIMUM = (3, 11)


class Failed(Exception):
    """A check that did not pass. Carries only its label; output already printed."""


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--python",
        nargs="+",
        metavar="X.Y",
        default=[],
        help="also run tests and mypy on these versions, e.g. 3.11 3.13",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="skip the build and the installed-package check",
    )
    parser.add_argument(
        "--no-venv",
        action="store_true",
        help="use the current interpreter instead of the .preflight environment",
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    try:
        python = _environment(no_venv=args.no_venv)
    except Failed:
        print("\nNot ready to push: the check environment could not be built.")
        return 1

    results: List[Tuple[str, bool]] = []
    for label, check in _checks(python, args):
        try:
            check()
        except Failed:
            results.append((label, False))
        else:
            results.append((label, True))

    return _report(results, time.monotonic() - started)


def _environment(*, no_venv: bool) -> str:
    """The interpreter the checks run with, built and populated if need be."""
    if no_venv:
        _headline("this interpreter")
        print(f"{sys.executable}, as asked. What is installed here is your business.")
        return sys.executable

    _headline("check environment")
    python = ENV_DIR / "bin" / "python"
    if not python.exists():
        base = _base_interpreter()
        print(f"building {ENV_DIR.name}/ from {base}, once")
        _run(
            "create the environment", [base, "-m", "venv", str(ENV_DIR)], headline=False
        )

    wanted = _stamp(str(python))
    if not STAMP.exists() or STAMP.read_text() != wanted:
        print("installing .[dev], build and twine")
        _run(
            "install the dependencies",
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "-e",
                ".[dev]",
                "build",
                "twine",
            ],
            headline=False,
        )
        STAMP.write_text(wanted)

    print(f"{python}, {_version_of(str(python))}")
    return str(python)


def _base_interpreter() -> str:
    """An interpreter new enough to build the environment from.

    Whatever ran this script is the first candidate, so the common case needs
    nothing installed and nothing chosen. A 3.10 or a conda base that cannot
    build this package is not a reason to stop, as long as some supported
    version is on PATH.
    """
    if sys.version_info[:2] >= MINIMUM:
        return sys.executable

    for version in SUPPORTED:
        found = shutil.which(f"python{version}")
        if found:
            return found

    current = ".".join(str(part) for part in sys.version_info[:2])
    print(
        f"this is Python {current}, and the package needs "
        f"{'.'.join(str(part) for part in MINIMUM)} or newer. "
        f"None of python{', python'.join(SUPPORTED)} is on PATH either."
    )
    raise Failed("check environment")


def _stamp(python: str) -> str:
    """What the environment was built from: this interpreter and these dependencies.

    Hashing pyproject rather than watching its timestamp, so a checkout or a
    branch switch that restores an identical file does not reinstall, and an
    edited dependency always does.
    """
    digest = hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()[:16]
    return f"{_version_of(python)} {digest}\n"


def _version_of(python: str) -> str:
    """The interpreter's own version, asked of it rather than assumed."""
    try:
        completed = subprocess.run(
            [
                python,
                "-c",
                "import sys; print('.'.join(map(str, sys.version_info[:3])))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"Python {completed.stdout.strip()}"


def _checks(
    python: str, args: argparse.Namespace
) -> List[Tuple[str, Callable[[], None]]]:
    """The checks to run, in the order a failure is most useful to see."""
    checks: List[Tuple[str, Callable[[], None]]] = [
        ("versions agree", _check_versions),
        ("tests", lambda: _run("tests", [python, "-m", "pytest", "tests/", "-q"])),
        ("mypy", lambda: _run("mypy", [python, "-m", "mypy", "src"])),
        ("black", lambda: _run("black", _black_argv(python))),
    ]

    if _tesseract_dirs():
        checks.append(
            ("tests without tesseract", lambda: _check_without_tesseract(python))
        )

    for version in args.python:
        checks.append((f"python {version}", lambda v=version: _check_version(v)))

    if not args.quick:
        checks.append(("built package", lambda: _check_package(python)))

    return checks


def _check_versions() -> None:
    """The gate `release.yml` runs, and the reason to run it before tagging.

    pyproject and server.json carry the version independently, and the registry
    rejects a submission whose version is not on PyPI - so a disagreement fails
    the release after the upload it cannot take back.
    """
    _headline("versions agree")
    release = _pyproject_version()
    server = json.loads((ROOT / "server.json").read_text())
    found = [("server.json", server["version"])] + [
        (f"server.json packages[{package['identifier']}]", package["version"])
        for package in server["packages"]
    ]

    bad = [f"{where} = {value}" for where, value in found if value != release]
    if bad:
        print(f"pyproject.toml = {release}, but " + ", ".join(bad))
        raise Failed("versions agree")

    print(f"{release} everywhere. Tag the release v{release}.")


def _check_without_tesseract(python: str) -> None:
    """Run the suite as the runner sees it: no tesseract anywhere.

    `release.yml` tests before it publishes, on a machine that has no tesseract,
    so a test that quietly needs the real binary fails the release rather than
    CI. Every OCR test fakes the binary; this is what keeps that true.
    """
    _headline("tests without tesseract")
    env = dict(os.environ)
    hidden = _tesseract_dirs()
    env["PATH"] = os.pathsep.join(
        entry for entry in env.get("PATH", "").split(os.pathsep) if entry not in hidden
    )
    env.pop("BENSPDF_TESSERACT", None)
    _run(
        "tests without tesseract",
        [python, "-m", "pytest", "tests/", "-q"],
        env=env,
        headline=False,
    )


def _check_version(version: str) -> None:
    """Tests and mypy on one interpreter from the CI matrix."""
    _headline(f"python {version}")
    interpreter = shutil.which(f"python{version}")
    if not interpreter:
        print(f"python{version} is not installed, so this version went unchecked")
        raise Failed(f"python {version}")

    with tempfile.TemporaryDirectory(prefix=f"preflight-{version}-") as work:
        env = Path(work) / "venv"
        _run(
            f"python {version} venv",
            [interpreter, "-m", "venv", str(env)],
            headline=False,
        )
        python = str(env / "bin" / "python")
        _run(
            f"python {version} install",
            [python, "-m", "pip", "install", "--quiet", "-e", ".[dev]"],
            headline=False,
        )
        _run(
            f"python {version} tests",
            [python, "-m", "pytest", "tests/", "-q"],
            headline=False,
        )
        _run(f"python {version} mypy", [python, "-m", "mypy", "src"], headline=False)


def _check_package(python: str) -> None:
    """Build, check the metadata, install the wheel clean, and serve MCP from it.

    The half of CI that the test suite cannot stand in for: an entry point that
    is not declared, a module left out of the wheel, a description PyPI will
    not render. Each of those has broken a release for somebody.
    """
    _headline("built package")
    with tempfile.TemporaryDirectory(prefix="preflight-package-") as work:
        dist = Path(work) / "dist"
        _run(
            "build",
            [python, "-m", "build", "--outdir", str(dist), str(ROOT)],
            headline=False,
        )

        artifacts = sorted(str(path) for path in dist.iterdir())
        print("\n".join(f"built {Path(path).name}" for path in artifacts))
        _run("twine check", [python, "-m", "twine", "check", "--strict", *artifacts])

        wheel = next(path for path in artifacts if path.endswith(".whl"))
        env = Path(work) / "venv"
        _run("clean venv", [python, "-m", "venv", str(env)], headline=False)
        installed = str(env / "bin" / "python")
        _run(
            "install the wheel",
            [installed, "-m", "pip", "install", "--quiet", wheel],
            headline=False,
        )
        _run(
            "verify it serves MCP",
            [
                installed,
                str(ROOT / "scripts" / "verify_install.py"),
                str(env / "bin" / "benspdf-mcp"),
            ],
        )


def _black_argv(python: str) -> List[str]:
    return [
        python,
        "-m",
        "black",
        "--target-version",
        BLACK_TARGET,
        "--check",
        "src",
        "tests",
        "scripts",
    ]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text()
    found = re.search(r'^version = "(.+)"$', text, flags=re.M)
    if not found:
        raise Failed("versions agree")
    return found.group(1)


def _tesseract_dirs() -> set:
    """PATH entries holding a tesseract, so they can be dropped rather than PATH replaced.

    Emptying PATH would hide the rest of the toolchain along with it, and then a
    green run would mean nothing.
    """
    found = set()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and (Path(entry) / "tesseract").exists():
            found.add(entry)
    return found


def _run(
    label: str,
    argv: Sequence[str],
    *,
    env: Optional[Dict[str, str]] = None,
    headline: bool = True,
) -> None:
    """Run one command in the repo root, showing its output as it goes."""
    if headline:
        _headline(label)
    try:
        completed = subprocess.run(argv, cwd=ROOT, env=env)
    except OSError as error:
        print(f"could not run {argv[0]}: {error}")
        raise Failed(label) from error
    if completed.returncode != 0:
        print(f"FAILED: {label}")
        raise Failed(label)


def _headline(label: str) -> None:
    print(f"\n\033[1m--- {label}\033[0m", flush=True)


def _report(results: Sequence[Tuple[str, bool]], seconds: float) -> int:
    failed = [label for label, passed in results if not passed]
    print("\n" + "-" * 60)
    for label, passed in results:
        print(f"{'pass' if passed else 'FAIL'}  {label}")
    print(f"{len(results) - len(failed)} of {len(results)} passed in {seconds:.0f}s")

    if failed:
        print("\nNot ready to push: " + ", ".join(failed))
        return 1
    print("\nReady to push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
