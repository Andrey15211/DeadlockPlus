"""PyInstaller standalone build: dist/DeadlockForge.exe (single-file, windowed).

Собирает по DeadlockForge.spec (web/ + data/ через datas, pywebview через
collect_all, иконка exe из web/assets/icons). run_desktop.py резолвит эти
каталоги из sys._MEIPASS и экспортирует DEADLOCK_WEB_DIR/DEADLOCK_DATA_DIR
до импорта FastAPI-приложения.

Спека обязательна не только ради иконки: её _strip_secrets выкидывает из
бандла api.txt с ключом LLM и пользовательскую SQLite-базу. CLI-сборка без
спеки эти файлы упаковывала.

Usage:
    python scripts/build_standalone.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRY = ROOT / "run_desktop.py"


def refresh_icons() -> None:
    """Перегенерировать иконки из мастер-арта в Assets/ перед упаковкой.

    Спека берёт готовый web/assets/icons/deadlockforge.ico, поэтому без этого
    шага в exe уезжает иконка от прошлой сборки: положил новый арт — получил
    старый ярлык. make_icon сам выбирает источник (.ico заказчика, иначе PNG)
    и нарезает 256/64/48/32/16 — одноразмерный .ico Windows мылит.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import make_icon

        make_icon.main()
    finally:
        sys.path.remove(str(ROOT / "scripts"))


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
    """Собрать exe по DeadlockForge.spec.

    Сборка идёт через спеку, а не через CLI-флаги: только спека прогоняет
    datas через _strip_secrets и не даёт упаковать api.txt с ключом LLM и
    пользовательскую SQLite-базу (они попадали в дистрибутив).
    """
    python = ensure_pyinstaller()
    spec = ROOT / "DeadlockForge.spec"
    if not spec.is_file():
        print(f"[build] не найдена спека: {spec}", file=sys.stderr)
        return 1
    refresh_icons()
    cmd = [python, "-m", "PyInstaller", "--noconfirm", "--clean", str(spec)]
    print("[build] " + " ".join(cmd))
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


if __name__ == "__main__":
    raise SystemExit(build())
