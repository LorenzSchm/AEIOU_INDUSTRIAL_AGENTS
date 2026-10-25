"""Recorded public openings only. No prior outcomes or private seller data are loaded."""

import hashlib
import json

from .config import state_root


def public_setups():
    path = state_root() / "public-setups.json"
    if not path.exists():
        return {}
    setups = json.loads(path.read_text())
    for setup in setups.values():
        value = {k: setup[k] for k in ("case", "threads")}
        if hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() != setup["sha256"]:
            raise ValueError("Public opening fixture hash mismatch")
    return setups
