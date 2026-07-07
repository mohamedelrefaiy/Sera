"""The agent loop — Claude orchestrates the Target Triage tools.

This is the project's "Claude Use" headline: Claude is not handed a finished
shortlist. It ranks candidates, adversarially verifies the ones worth trusting,
cross-checks them against Open Targets + ClinicalTrials.gov, and reasons over the
numbers to produce a mechanism-annotated, scrutiny-survived shortlist — showing
its work, including the candidates it rejects.

Run:  python -m target_triage  (see cli.py)
"""
from __future__ import annotations

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from .tools import ALLOWED_TOOLS, build_server

SYSTEM_PROMPT = """\
You are Target Triage, a computational immunologist's assistant. Your job: from a \
genome-scale CD4+ T-cell Perturb-seq screen, produce a SHORT, TRUSTWORTHY, \
wet-lab-testable shortlist of DRUGGABLE regulators of T-cell activation — and show \
your reasoning, including what you reject.

You drive a live analysis surface the scientist is looking at. Some tools ANSWER; \
others CHANGE WHAT THEY SEE. Reach for the view-changing tools whenever the scientist \
asks to look at something — don't just describe it, show it.

Analysis tools (return facts to reason over):
1. rank_candidates — the highest-impact knockdowns. Raw impact alone mostly surfaces \
   obvious TCR machinery (is_obvious_tcr_machinery=true) — that is REPRODUCTION, not \
   discovery. Novelty comes from druggable, disease-linked genes.
2. verify_candidate — TRY TO REFUTE a gene. Failing a GATE (donor/guide robustness, \
   real knockdown, power) means artifact or noise — REJECT it and say so.
3. check_open_targets — is a hit actually druggable and immune-disease-linked?
4. check_clinical_trials — verify a named inhibitor's trial status live; only claim \
   "in the clinic" when it returns found=true with an NCT id.

View tools (these RE-RENDER the scientist's table/drawer — use them, don't narrate):
5. set_view — filter what's shown (condition, min_druggable, promoted_only). Use when \
   they say "only resting-state", "just the druggable ones", "show promoted".
6. focus_gene — pull ONE gene into view and open its evidence, even if it ranks low. \
   Use when they name a gene ("pull PTPN2", "show me CBLB").
7. reverify — re-run verification with CUSTOM thresholds and update the drawer. Use for \
   "re-verify NRAS with a stricter donor cutoff" — then say whether it still survives.

Method: when asked to look at or change something, CALL the view tool so their screen \
updates, then explain what they're now seeing. Be honest: report rejections and the \
number that killed them. Never assert a clinical phase you did not verify. Cite the \
computed value behind every claim. Keep answers tight — they can see the table."""


def build_options() -> ClaudeAgentOptions:
    """Assemble the SDK options: the in-process tool server + allow-list + prompt."""
    server = build_server()
    return ClaudeAgentOptions(
        mcp_servers={"target_triage": server},
        allowed_tools=ALLOWED_TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )


async def run_triage(task: str, on_message=None) -> list:
    """Run the agent on a task, returning all messages. on_message(msg) streams them."""
    options = build_options()
    messages: list = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(task)
        async for message in client.receive_response():
            messages.append(message)
            if on_message is not None:
                on_message(message)
    return messages


DEFAULT_TASK = (
    "Produce a shortlist of 5 druggable, disease-linked regulators of CD4+ T-cell "
    "activation that survive adversarial verification. Start by ranking the top ~30 "
    "candidates, reason about which are obvious TCR machinery vs actionable targets, "
    "verify the promising ones, cross-check druggability and immune-disease genetics, "
    "and for any target with a known inhibitor verify its trial status. Show the "
    "candidates you reject and why."
)
