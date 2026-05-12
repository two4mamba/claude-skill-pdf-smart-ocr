"""Probe for the mineru CLI using the SAME resolver as extract.py.

Purpose: give Claude (and humans) a single, authoritative way to ask
"is MinerU available, and where?" without re-implementing the lookup.

Why this exists: a bare `where mineru` / `mineru --version` is the WRONG
way to detect MinerU on this skill. The resolver (`_resolve_mineru_exe`
in extract.py) tries three locations in order:

    1. MINERU_EXE env var (overrides everything)
    2. PATH lookup (`where.exe mineru`)
    3. Windows fallback `C:\\mineru-venv\\Scripts\\mineru.exe`

A caller that only checks step 2 will incorrectly conclude "not installed"
when MinerU is actually reachable via step 1 or step 3. Use this script
as the source of truth instead.

Usage
-----
    python scripts/check_mineru.py            # human-readable
    python scripts/check_mineru.py --json     # machine-readable

Exit codes
----------
    0  MinerU is available; resolved path printed on stdout.
    1  MinerU is NOT available; actionable install hint on stderr.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _import_resolver():
    """Pull `_resolve_mineru_exe` from extract.py without running its CLI."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from extract import _resolve_mineru_exe  # type: ignore[import-not-found]
    return _resolve_mineru_exe


def _probe_version(exe: str, timeout: float = 10.0) -> str | None:
    """Best-effort `<exe> --version`. Returns the first non-empty line on
    success, None on any failure (missing file, directory, non-executable,
    crash, timeout, non-zero exit). This is the single authoritative test
    for "is this MinerU actually runnable?" — path existence is not enough
    (a directory or text file can pass `Path.exists()` but blow up at
    subprocess.run time, leading to a false-positive availability report).
    """
    try:
        out = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    line = (out.stdout or out.stderr or "").strip().splitlines()
    return line[0] if line else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect the MinerU CLI for this skill.")
    ap.add_argument("--json", action="store_true",
                    help="Emit a JSON object on stdout instead of human-readable lines.")
    args = ap.parse_args()

    resolve = _import_resolver()
    exe = resolve()
    # Authoritative availability test: try to actually run `<exe> --version`.
    # Path existence is insufficient — MINERU_EXE could point at a directory
    # or a non-executable file, and `where mineru` could leave a stale entry.
    # If the binary won't execute cleanly, the skill itself would fail at
    # runtime, so we must report unavailable here too.
    version = _probe_version(exe)
    found = version is not None

    result = {
        "available": bool(found),
        "path": exe if found else None,
        "version": version,
        "resolver_steps": [
            "MINERU_EXE env var",
            "PATH lookup (where.exe mineru)",
            "Windows fallback C:\\mineru-venv\\Scripts\\mineru.exe",
        ],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        if found:
            print(f"mineru: {exe}")
            if version:
                print(f"version: {version}")
        else:
            sys.stderr.write(
                "mineru NOT found via any resolver step.\n"
                "  Tried: MINERU_EXE env var, PATH, C:\\mineru-venv\\Scripts\\mineru.exe\n"
                "Fix one of:\n"
                "  - install mineru:  pip install -U \"mineru[core]\"\n"
                "  - set env var:     "
                "[Environment]::SetEnvironmentVariable('MINERU_EXE',"
                "'<full path to mineru.exe>','User')\n"
            )

    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
