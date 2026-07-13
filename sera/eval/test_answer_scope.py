"""ANSWER SCOPE GATE — the conversational answer must never leak a removed product or offer to
act as a coding assistant.

Sera's /api/answer prompt tells the model to stay in scope (immunology gene reconciliation),
but a prompt is probabilistic: on an off-topic or product-name question the model occasionally
names the removed "Sera" product or offers to "read the codebase". Those must NEVER reach
the browser, so api/app._scrub_answer is a DETERMINISTIC backstop that replaces any such reply with
a safe redirect. This gate pins that backstop so a future prompt/model change can't silently
re-open the leak.

These tests are pure — they exercise `_scrub_answer` directly with no live model call, so they run
offline with no credentials.

Run under pytest: pytest eval/test_answer_scope.py
"""
from __future__ import annotations

from sera.api.app import _scrub_answer

# Replies the live model actually produced for off-topic / product-name questions (observed
# 2026-07-12). Each must be scrubbed — none may reach the user verbatim.
LEAKING_REPLIES = (
    "Sera is the reusable immunology tool you're building for the hackathon.",
    "Sera (the reconciliation interface), Sera (the broader platform), or something else?",
    "I can help you understand the codebase structure. Let me read the project layout.",
    "This looks like a train/test split in the pipeline — which frontend do you mean?",
)

# On-topic answers the tool SHOULD deliver unchanged — the backstop must not over-scrub the very
# content the product exists to explain.
CLEAN_REPLIES = (
    "In Sera, 'discordant' means both screens fired but disagree on the direction of the effect.",
    "Try: reconcile TSC1 to see the verdict, effect sizes, and druggability.",
    "The mRNA screen reads transcript; the protein screen reads secreted protein via FACS.",
    "A protein-only verdict means the protein moved while the transcript did not.",
)


def test_leaking_replies_are_scrubbed():
    """Every observed off-scope reply is replaced. A None here means a leak reaches the browser."""
    for reply in LEAKING_REPLIES:
        safe = _scrub_answer(reply)
        assert safe is not None, f"backstop let an off-scope reply through: {reply!r}"
        assert "target triage" not in safe.lower(), "the replacement itself names the removed product"
        assert "codebase" not in safe.lower(), "the replacement itself offers coding help"


def test_on_topic_replies_pass_through():
    """The backstop must not fire on legitimate reconciliation answers, or it would gut the tool."""
    for reply in CLEAN_REPLIES:
        assert _scrub_answer(reply) is None, f"backstop over-scrubbed an on-topic answer: {reply!r}"


def test_product_name_is_caught_case_insensitively():
    """The scan is case-insensitive: 'TARGET TRIAGE' / 'sera' must both trip it."""
    for variant in ("TARGET TRIAGE", "Target_Triage", "the target triage platform"):
        assert _scrub_answer(f"You could use {variant} for that.") is not None, variant


if __name__ == "__main__":
    test_leaking_replies_are_scrubbed()
    test_on_topic_replies_pass_through()
    test_product_name_is_caught_case_insensitively()
    print("PASS — answer scope backstop holds")
