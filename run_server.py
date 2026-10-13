"""Start the DeadlockForge server on 127.0.0.1:8377.

Works from any working directory: the src/ layout is put on sys.path
relative to this file's location, not the CWD.
"""

from __future__ import annotations

import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import uvicorn

from deadlockforge.api.app import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8377)
