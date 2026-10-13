"""Compact build code codec: BuildSlots ⇄ Base64 string.

Interface:
    encode(slots: BuildSlots) -> str
    decode(code: str) -> BuildSlots

Wire format (deterministic, URL-safe):
    {"weapon": [...4...], "vitality": [...], "spirit": [...], "flex": [...]}
    → json.dumps(..., separators=(",", ":"), ensure_ascii=False) → UTF-8
    → base64.urlsafe_b64encode → ASCII str.

decode() is strict: bad Base64, bad JSON, non-object JSON or a payload that
does not satisfy the BuildSlots schema all raise BuildError. Unknown item ids
are NOT checked here — that is a dataset concern; callers (e.g. the API
layer) run builds_mod.validate on the decoded slots.
"""

from __future__ import annotations

import base64
import binascii
import json

from pydantic import ValidationError

from ..models import BuildError, BuildSlots

_SLOT_KEYS: tuple[str, ...] = ("weapon", "vitality", "spirit")


def encode(slots: BuildSlots) -> str:
    """Serialize a BuildSlots into a compact URL-safe Base64 code."""
    payload = {key: getattr(slots, key) for key in _SLOT_KEYS}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode(code: str) -> BuildSlots:
    """Parse a build code back into a validated BuildSlots.

    Raises:
        BuildError: on invalid Base64, invalid JSON, a non-object JSON
            payload or a payload that fails BuildSlots validation.
    """
    if not isinstance(code, str):
        raise BuildError(
            f"Build code must be a string, got {type(code).__name__}."
        )
    if not code:
        raise BuildError("Build code is empty.")
    try:
        raw = base64.urlsafe_b64decode(code.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise BuildError(f"Build code is not valid Base64: {exc}.") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"Build code payload is not valid JSON: {exc}.") from exc
    if not isinstance(payload, dict):
        raise BuildError(
            "Build code payload must be a JSON object with slot groups, "
            f"got {type(payload).__name__}."
        )

    # Normalize slot arrays to 12 slots each
    for key in ("weapon", "vitality", "spirit"):
        slots_list = payload.get(key) or []
        if not isinstance(slots_list, list):
            raise BuildError(f"Slot group {key!r} must be a list.")
        if len(slots_list) < 12:
            slots_list = list(slots_list) + [None] * (12 - len(slots_list))
        payload[key] = slots_list[:12]

    try:
        return BuildSlots.model_validate(payload)
    except ValidationError as exc:
        raise BuildError(f"Build code payload does not match slot schema: {exc}.") from exc

