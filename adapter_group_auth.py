"""Group @ Hermes pairing bypass helpers (no Hermes gateway imports)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_DEFAULT_PAIRING_FILE = Path.home() / ".hermes" / "pairing" / "miti-approved.json"


def resolve_group_auth_user_id(
    configured: str = "",
    pairing_path: Optional[Path] = None,
) -> tuple[str, str]:
    """Resolve the paired user ID used for group @ Gateway auth.

    Priority:
      1. ``MITI_OWNER_USER_ID`` env / explicit config (non-empty)
      2. Sole entry in ``~/.hermes/pairing/miti-approved.json``
      3. Empty — group @ falls back to each sender's own user_id (must be paired)

    Returns ``(user_id, source)`` where source is ``"env"``, ``"pairing"``, or ``""``.
    """
    explicit = (configured or "").strip()
    if explicit:
        return explicit, "env"

    path = pairing_path or _DEFAULT_PAIRING_FILE
    if os.getenv("HERMES_HOME"):
        alt = Path(os.environ["HERMES_HOME"]) / "pairing" / "miti-approved.json"
        if alt.exists():
            path = alt

    if not path.is_file():
        return "", ""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "", ""

    if not isinstance(data, dict) or not data:
        return "", ""

    user_ids = [str(uid).strip() for uid in data if str(uid).strip()]
    if len(user_ids) == 1:
        return user_ids[0], "pairing"
    return "", ""


def group_session_user_ids(
    sender_id: str,
    owner_user_id: str,
) -> tuple[str, Optional[str]]:
    """Map group @ sender to Hermes gateway session IDs.

    Hermes Gateway pairing checks ``SessionSource.user_id``. Group members are
    not required to pair individually; the owner's paired ID passes auth while
    ``user_id_alt`` keeps per-sender session isolation inside the group.
    """
    owner = (owner_user_id or "").strip()
    sender = (sender_id or "").strip()
    if not owner or not sender or owner == sender:
        return sender, None
    return owner, sender
