"""Progress reporting for the AI-assisted agent and skill builders.

Both builders ask the model for a single JSON envelope, so the raw token stream
cannot be shown to the user. What can be shown is derived from the partially
received text: which stage the model has reached, and the visible message once
it starts arriving.
"""

from __future__ import annotations

import json
from typing import Any, Dict

_ASSISTANT_KEY = '"assistant_message"'
_DRAFT_KEY = '"draft"'
_INSTRUCTIONS_KEYS = ('"system_prompt"', '"content"')


def _stage(buffer: str) -> str:
    """Name the phase the model is in, based on the keys already emitted."""
    if _DRAFT_KEY in buffer:
        if any(key in buffer for key in _INSTRUCTIONS_KEYS):
            return "writing_instructions"
        return "drafting"
    if _ASSISTANT_KEY in buffer:
        return "replying"
    return "analyzing"


def _partial_assistant_message(buffer: str) -> str:
    """Return the visible message received so far, even mid-string."""
    key_at = buffer.find(_ASSISTANT_KEY)
    if key_at < 0:
        return ""

    opening = buffer.find('"', key_at + len(_ASSISTANT_KEY))
    if opening < 0:
        return ""

    index = opening + 1
    length = len(buffer)
    while index < length:
        char = buffer[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            break
        index += 1

    fragment = buffer[opening + 1 : index]
    # A trailing lone backslash opens an escape sequence whose second half has
    # not arrived yet; leaving it in would break the decoder.
    if fragment.endswith("\\") and not fragment.endswith("\\\\"):
        fragment = fragment[:-1]
    try:
        return str(json.loads(f'"{fragment}"'))
    except json.JSONDecodeError:
        return ""


def partial_progress(buffer: str) -> Dict[str, Any]:
    """Derive honest progress from a partially received JSON reply."""
    return {
        "stage": _stage(buffer),
        "assistant_message": _partial_assistant_message(buffer),
    }
