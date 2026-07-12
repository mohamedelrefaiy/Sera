"""Chat session store: persist a chat as an ordered list of TURN DESCRIPTORS so it can be
re-opened later and replayed through the exact same frontend renderer that built it live.

A descriptor is small data, never rendered HTML: {role, text, intent, gene, ts}. On reopen the
browser replays these through its own dispatch functions, so this module never knows about the
UI — it only owns the on-disk format. app.py calls the CRUD helpers; nothing else touches files.

Format: one JSON file per session under application/data/sessions/<id>.json, shaped:
    {"id": <hex>, "title": <str>, "created": <float>, "updated": <float>, "turns": [<descriptor>, ...]}

Mirrors runlog.py's discipline: filesystem-safe uuid ids, immutable updates (helpers return new
dicts / re-read+write rather than mutating a passed-in object in place), and write failures are
caught and surfaced to stderr rather than propagated — a persistence hiccup must never break chat.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

# data/sessions/ lives under application/ (this file is application/target_triage/api/sessions.py,
# so three dirname() hops up reaches application/).
SESSIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "sessions",
)

# A session title is the first user message, trimmed to this many characters.
_TITLE_MAX = 60
# Hard cap on turns per session — a runaway loop can't grow a file without bound.
_TURNS_MAX = 500


def _path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def _valid_id(session_id: str) -> bool:
    """Only accept our own hex ids — blocks path traversal (no '/', '..', or extensions)."""
    return bool(session_id) and session_id.isalnum() and len(session_id) <= 40


def _read(session_id: str) -> dict | None:
    if not _valid_id(session_id):
        return None
    try:
        with open(_path(session_id), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    except OSError as e:  # noqa: BLE001 — surface but don't crash the request
        print(f"[sessions] read failed for {session_id}: {e}", file=sys.stderr)
        return None


def _write(session: dict) -> bool:
    """Atomically write a session dict. Returns True on success, False on failure (never raises)."""
    try:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        p = _path(session["id"])
        tmp = f"{p}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False)
        os.replace(tmp, p)  # atomic: readers never see a half-written file
        return True
    except OSError as e:  # noqa: BLE001
        print(f"[sessions] write failed for {session.get('id')}: {e}", file=sys.stderr)
        return False


def create(title: str = "") -> dict:
    """Create a fresh empty session and persist it. Returns the new session dict."""
    now = time.time()
    session = {
        "id": uuid.uuid4().hex,
        "title": (title or "New chat")[:_TITLE_MAX],
        "created": now,
        "updated": now,
        "turns": [],
    }
    _write(session)
    return session


def get(session_id: str) -> dict | None:
    """Full session (with turns) for replay, or None if it doesn't exist."""
    return _read(session_id)


def list_sessions(limit: int = 50) -> list[dict]:
    """Session summaries, newest-updated first. Summary omits `turns` (list view doesn't need them)."""
    out: list[dict] = []
    try:
        names = os.listdir(SESSIONS_DIR)
    except FileNotFoundError:
        return []
    except OSError as e:  # noqa: BLE001
        print(f"[sessions] list failed: {e}", file=sys.stderr)
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        s = _read(name[:-5])
        if not s:
            continue
        # Skip empty sessions in the list — a chat with no turns isn't worth showing.
        if not s.get("turns"):
            continue
        out.append({
            "id": s["id"],
            "title": s.get("title") or "New chat",
            "created": s.get("created", 0),
            "updated": s.get("updated", 0),
            "turn_count": len(s.get("turns", [])),
        })
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out[:limit]


def append_turn(session_id: str, turn: dict) -> dict | None:
    """Append one turn descriptor (immutably: read → new dict → write). Sets the title from the
    first USER turn if the session is still untitled. Returns the updated summary, or None."""
    s = _read(session_id)
    if s is None:
        return None
    turns = list(s.get("turns", []))
    if len(turns) >= _TURNS_MAX:
        return {"id": s["id"], "title": s.get("title"), "turn_count": len(turns),
                "updated": s.get("updated", 0), "capped": True}
    stamped = {**turn, "ts": time.time()}
    turns.append(stamped)
    title = s.get("title") or "New chat"
    if title in ("", "New chat") and turn.get("role") == "user" and turn.get("text"):
        title = str(turn["text"])[:_TITLE_MAX]
    updated = {**s, "turns": turns, "title": title, "updated": time.time()}
    _write(updated)
    return {"id": updated["id"], "title": updated["title"], "turn_count": len(turns),
            "updated": updated["updated"]}


def set_title(session_id: str, title: str) -> bool:
    s = _read(session_id)
    if s is None:
        return False
    updated = {**s, "title": (title or "New chat")[:_TITLE_MAX], "updated": time.time()}
    return _write(updated)


def delete(session_id: str) -> bool:
    if not _valid_id(session_id):
        return False
    try:
        os.remove(_path(session_id))
        return True
    except FileNotFoundError:
        return False
    except OSError as e:  # noqa: BLE001
        print(f"[sessions] delete failed for {session_id}: {e}", file=sys.stderr)
        return False
