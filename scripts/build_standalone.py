"""PyInstaller standalone build: dist/DeadlockForge.exe (single-file, windowed).

Installs pyinstaller on first run, then bundles web/ and data/ via
--add-data and pywebview runtime files via --collect-all. run_desktop.py
resolves those from sys._MEIPASS at runtime and exports
DEADLOCK_WEB_DIR/DEADLOCK_DATA_DIR before importing the FastAPI app.

Usage:
    python scripts/build_standalone.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRY = ROOT / "run_desktop.py"


def ensure_pyinstaller() -> str:
    """Import-check pyinstaller, pip-install it if missing. Returns command."""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build] pyinstaller not found, installing...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=True,
        )
    return sys.executable


def build() -> int:
    python = ensure_pyinstaller()
    cmd = [
        python, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "DeadlockForge",
        "--paths", str(ROOT / "src"),
        "--add-data", f"{ROOT / 'web'};web",
        "--add-data", f"{ROOT / 'data'};data",
        "--collect-all", "deadlockforge",
        "--collect-all", "pywebview",
        str(ENTRY),
    ]
    print("[build] " + " ".join(cmd))
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


if __name__ == "__main__":
    raise SystemExit(build())
