"""Regression tests for multi-turn agent context.

The SDK client is intentionally short-lived per request, so the API must reconstruct the
conversation from the persisted browser session before sending a follow-up to the agent.
"""
from __future__ import annotations

import importlib

from target_triage.api.app import _build_contextual_chat_task

app_module = importlib.import_module("target_triage.api.app")


def test_follow_up_includes_previous_exchange(monkeypatch):
    session = {
        "turns": [
            {"role": "user", "text": "Is PTPN2 druggable?"},
            {"role": "agent", "text": "PTPN2 is moderately tractable. Want the reconciliation verdict next?"},
            # The frontend persists the current user message before opening the chat stream.
            {"role": "user", "text": "yes"},
        ]
    }
    monkeypatch.setattr(app_module.sessions_store, "get", lambda sid: session)

    task = _build_contextual_chat_task("yes", "session123")

    assert "Is PTPN2 druggable?" in task
    assert "Want the reconciliation verdict next?" in task
    assert '"text": "yes"' not in task, "the current user message must not be duplicated in history"
    assert 'current_user_message_json: "yes"' in task
    assert "Continue the conversation" in task


def test_new_chat_without_session_stays_context_free():
    assert _build_contextual_chat_task("Reconcile TSC1", None) == "Reconcile TSC1"


def test_history_is_bounded(monkeypatch):
    turns = [{"role": "user", "text": f"message {i}"} for i in range(40)]
    monkeypatch.setattr(app_module.sessions_store, "get", lambda sid: {"turns": turns})

    task = _build_contextual_chat_task("latest", "session123")

    assert "message 0" not in task
    assert "message 39" in task
    assert len(task) < 20_000
