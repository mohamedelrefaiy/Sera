"""SESSION-REPLAY PARITY GATE — reopening a saved chat must render what was PERSISTED.

Distinct from test_replay.py (which guards the runlog/SSE "replay spine" byte-for-byte).
This gate guards the SIDEBAR reopen path — the one a user actually clicks — implemented in
the frontend's openSession(). The bug this locks down: the write path saved every turn with
intent:"agent" (carrying the resolved gene + final narration), but the read path re-dispatched
through the OLD fixed router (dispatchIntent), which has no "agent" branch. Agent turns were
dropped and user turns re-streamed a generic /api/answer reply — so reopened chats showed the
wrong content, or a "Working" spinner that never settled.

The fix reconstructs each turn from its descriptor instead of re-executing the agent. Since the
replay logic is browser JS (no headless JS runtime in this eval suite), this gate asserts the
structural invariants of that logic by parsing concord-app.html. They are cheap, deterministic,
and fail loudly if openSession ever regresses back to a re-dispatch-only replay.

Run as a gate:      python eval/test_session_replay_frontend.py   (prints PASS/FAIL)
Run under pytest:   pytest eval/test_session_replay_frontend.py
"""
from __future__ import annotations

import os
import re
import sys

_FRONTEND = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "concord-app.html"
)


def _html() -> str:
    with open(_FRONTEND, encoding="utf-8") as fh:
        return fh.read()


def _slice(html: str, fn_name: str) -> str:
    """Return the source of a top-level `async function <fn_name>(` or `function <fn_name>(`
    body by brace-matching from its opening `{`. Good enough for these single-definition
    helpers; raises if the function is missing (that itself is a regression)."""
    m = re.search(r"(?:async\s+)?function\s+" + re.escape(fn_name) + r"\s*\(", html)
    assert m, f"{fn_name}() not found in concord-app.html"
    i = html.index("{", m.end() - 1)
    depth, j = 0, i
    while j < len(html):
        c = html[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return html[i : j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces while slicing {fn_name}()")


# --- gate 1: openSession renders agent turns, it does not drop them ---

def check_replay_uses_agent_turns() -> tuple[bool, str]:
    html = _html()
    body = _slice(html, "openSession")

    # The old broken replay skipped every non-user turn (`if(t.role!=="user") continue;`) AND had
    # no way to render an agent turn. The fix pairs a user turn with the agent turn that follows and
    # renders the saved narration via replayAgentTurn. Assert the read path consumes agent turns.
    if "replayAgentTurn(" not in body:
        return False, "openSession no longer calls replayAgentTurn — agent turns would be dropped"
    if 'next.role==="agent"' not in body.replace(" ", ""):
        return False, "openSession no longer pairs a user turn with its following agent turn"
    return True, "openSession pairs user+agent turns and renders saved narration"


# --- gate 2: replay reconstructs from saved text, it does not re-run the agent ---

def check_replay_does_not_rerun_agent() -> tuple[bool, str]:
    html = _html()
    body = _slice(html, "replayAgentTurn")

    # Faithful replay must NOT hit the live agent (/api/chat) or the answer stream (/api/answer):
    # that would produce fresh, non-deterministic content and could hang on a stalled stream (the
    # eternal-"Working" symptom). It renders the SAVED narration text directly.
    if "api/chat" in body or "runAgent(" in body:
        return False, "replayAgentTurn re-runs the live agent instead of rendering saved text"
    if "api/answer" in body or "appendAnswerTurn(" in body:
        return False, "replayAgentTurn re-streams a fresh answer instead of the saved narration"
    if "el.textContent" not in body:
        return False, "replayAgentTurn does not write the saved narration into the bubble"
    # A finished run must show a settled trace, never a live spinner.
    if ".hidden=true" not in body.replace(" ", ""):
        return False, "replayAgentTurn does not hide the live 'Working' activity spinner"
    return True, "replayAgentTurn renders saved narration + deterministic figure, no live calls"


# --- gate 3: a hard refresh restores the last chat instead of blanking the thread ---

def check_refresh_restores_last_session() -> tuple[bool, str]:
    html = _html()
    m = re.search(r"function\s+init\s*\(", html)
    assert m, "init() not found"
    # init() is an IIFE: (async function init(){ ... })(); grab up to its closing "})();".
    start = html.index("{", m.end() - 1)
    end = html.index("})();", start)
    body = html[start:end]

    if "openSession(" not in body:
        return False, "init() no longer restores a saved session on load — refresh blanks the thread"
    if "api/sessions" not in body:
        return False, "init() no longer reads the session list to find the most-recent chat"
    return True, "init() restores the most-recent saved chat when there is no ?gene= deep-link"


# --- pytest entry points ---

def test_replay_uses_agent_turns() -> None:
    passed, reason = check_replay_uses_agent_turns()
    assert passed, reason


def test_replay_does_not_rerun_agent() -> None:
    passed, reason = check_replay_does_not_rerun_agent()
    assert passed, reason


def test_refresh_restores_last_session() -> None:
    passed, reason = check_refresh_restores_last_session()
    assert passed, reason


if __name__ == "__main__":
    print("SESSION-REPLAY PARITY GATE — reopening a chat must render what was persisted\n")
    checks = (
        ("openSession renders agent turns", check_replay_uses_agent_turns),
        ("replay reconstructs (no live re-run)", check_replay_does_not_rerun_agent),
        ("refresh restores the last chat", check_refresh_restores_last_session),
    )
    results = [(name, *fn()) for name, fn in checks]
    width = max(len(name) for name, _, _ in results)
    for name, passed, reason in results:
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {name:<{width}} — {reason}")
    n_pass = sum(passed for _, passed, _ in results)
    print(f"\n{n_pass}/{len(results)} replay-parity checks passed.")
    sys.exit(0 if n_pass == len(results) else 1)
