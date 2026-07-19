#!/usr/bin/env python
"""Locates ComfyUI installations and their Python environments on disk."""

import os
import subprocess
import sys

# Oldest Python current ComfyUI requirements resolve on (comfy-kitchen and
# av>=16 publish no wheels for anything older).
MIN_COMFY_PYTHON = (3, 10)


def get_python_version(py_path):
    """(major, minor) of the interpreter at py_path, or None if unqueryable."""
    try:
        result = subprocess.run(
            [py_path, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            major, minor = result.stdout.split()
            return int(major), int(minor)
    except Exception:
        pass
    return None


def _python_in(env_dir):
    if sys.platform.startswith("win"):
        candidate = os.path.join(env_dir, "Scripts", "python.exe")
    else:
        candidate = os.path.join(env_dir, "bin", "python")
    return candidate if os.path.isfile(candidate) else None


def find_comfy_root(directory):
    """
    Returns the ComfyUI repo root at/inside `directory`, or None.
    Accepts the repo itself, a parent containing a `ComfyUI/` subfolder
    (the installer wizard's layout, also the Windows portable bundle).
    A root is recognized by having both main.py and the `comfy` package.
    """
    directory = os.path.abspath(directory or "")
    for candidate in (directory, os.path.join(directory, "ComfyUI")):
        if (os.path.isfile(os.path.join(candidate, "main.py"))
                and os.path.isdir(os.path.join(candidate, "comfy"))):
            return candidate
    return None


def find_comfy_python(root):
    """
    Best-effort discovery of the Python a ComfyUI install runs with:
    venv/.venv inside the repo or next to it, else the portable bundle's
    python_embeded. Returns None when nothing is found.
    """
    if not root:
        return None
    parent = os.path.dirname(root)
    for env_dir in (os.path.join(root, "venv"),
                    os.path.join(root, ".venv"),
                    os.path.join(parent, "venv"),
                    os.path.join(parent, ".venv")):
        python = _python_in(env_dir)
        if python:
            return python
    # ComfyUI_windows_portable layout: ComfyUI/ and python_embeded/ side by side.
    embedded = os.path.join(parent, "python_embeded",
                            "python.exe" if sys.platform.startswith("win") else "python")
    if os.path.isfile(embedded):
        return embedded
    return None


def detect_comfy_installation(directory):
    """Returns {"root", "main_py", "python"} (python may be None), or None."""
    root = find_comfy_root(directory)
    if root is None:
        return None
    return {
        "root": root,
        "main_py": os.path.join(root, "main.py"),
        "python": find_comfy_python(root),
    }
