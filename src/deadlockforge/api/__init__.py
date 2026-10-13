"""DeadlockForge HTTP API — thin adapter over the engine modules."""

from __future__ import annotations

from .app import WEB_DIR, CalcRequest, app, create_app

__all__ = ["app", "create_app", "CalcRequest", "WEB_DIR"]
