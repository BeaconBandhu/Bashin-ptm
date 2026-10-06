"""Reference numbers for filed disputes — distinct from the 8-digit support
ticket_id, matching how a real bank dispute gets its own case number
separate from the support ticket that raised it."""

from __future__ import annotations

import secrets


def generate_dispute_reference() -> str:
    return f"DSP-{secrets.token_hex(4).upper()}"
