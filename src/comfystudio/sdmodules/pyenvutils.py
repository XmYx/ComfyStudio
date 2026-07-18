#!/usr/bin/env python
"""Helpers for discovering Python interpreters installed on the machine.

Used when creating virtual environments so they are built with the newest
Python version available instead of whatever "python"/"python3" happens to be.
"""

import os
import shutil
import subprocess
import sys

# Checked in addition to the generic names; extend as new versions release.
_VERSIONED_NAMES = [
    "python3.15", "python3.14", "python3.13", "python3.12",
    "python3.11", "python3.10", "python3.9",
]


def python_version(exe) -> tuple:
    """Returns the (major, minor, micro) of an interpreter, or None if unusable."""
    try:
        result = subprocess.run(
            [exe, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(".")
            return tuple(int(p) for p in parts[:3])
    except Exception:
        pass
    return None


def discover_pythons() -> list:
    """Returns candidate Python executables found on this machine (deduplicated)."""
    candidates = []
    seen = set()

    def add(path):
        if not path:
            return
        real = os.path.realpath(path)
        if real not in seen and os.path.isfile(real):
            seen.add(real)
            candidates.append(path)

    for name in _VERSIONED_NAMES + ["python3", "python"]:
        add(shutil.which(name))

    if sys.platform.startswith("win"):
        # The Windows launcher knows about every registered installation.
        try:
            result = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=10)
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if parts and parts[-1].lower().endswith("python.exe"):
                    add(parts[-1])
        except Exception:
            pass

    add(sys.executable)
    return candidates


def find_latest_python():
    """
    Returns (path, version_tuple) of the newest Python interpreter found,
    or (None, None) when nothing usable exists.
    """
    best, best_ver = None, None
    for exe in discover_pythons():
        ver = python_version(exe)
        if ver and (best_ver is None or ver > best_ver):
            best, best_ver = exe, ver
    return best, best_ver
