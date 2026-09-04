"""Ingest: four channels in, one internal representation out (A2).

Handles missing fields, empty bodies and unusual characters without failing,
and preserves both the original text and the channel because both matter
downstream.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any, Iterator

from .models import CHANNELS, Ticket

# Channel aliases seen across the four sources. Unknown channels are kept
# rather than dropped, and flagged by the caller.
_ALIASES = {
    "e-mail": "email",
    "mail": "email",
    "live_chat": "chat",
    "livechat": "chat",
    "docs": "docs_comment",
    "documentation_comment": "docs_comment",
    "community": "forum",
    "community_forum": "forum",
}


def normalise_channel(value: Any) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_")
    raw = _ALIASES.get(raw, raw)
    return raw if raw in CHANNELS else (raw or "unknown")


def clean_text(value: Any) -> str:
    """NFKC-normalise, strip control characters, collapse whitespace.

    Chat and forum bodies carry zero-width and control characters that break
    naive tokenisation; email carries hard-wrapped lines.
    """
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(ch for ch in text if ch == "\n" or not unicodedata.category(ch).startswith("C"))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def normalise(record: dict[str, Any], index: int = 0) -> Ticket:
    """One raw record to one Ticket. Never raises on a malformed record."""
    if not isinstance(record, dict):
        record = {"body": str(record)}
    ticket_id = str(record.get("ticket_id") or "").strip() or f"UNKNOWN-{index:05d}"
    return Ticket(
        ticket_id=ticket_id,
        channel=normalise_channel(record.get("channel")),
        subject=clean_text(record.get("subject")),
        body=clean_text(record.get("body")),
        received_at=str(record.get("received_at") or ""),
        customer_id=str(record.get("customer_id") or "unknown"),
        customer_tier=str(record.get("customer_tier") or "unknown"),
        customer_region=str(record.get("customer_region") or "unknown"),
        language_fluency=str(record.get("language_fluency") or "unknown"),
        raw=record,
    )


def load_tickets(path: str | Path) -> Iterator[Ticket]:
    """Stream tickets from a JSON array or JSONL file.

    Accepts whatever shape the hidden set arrives in: a bare array, or an
    object wrapping one under "tickets"/"data"/"items".
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, dict):
        for key in ("tickets", "data", "items", "records"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    for i, record in enumerate(data):
        yield normalise(record, i)
