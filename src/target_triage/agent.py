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

You have four tools. Use them deliberately, in this spirit:
1. rank_candidates — get the highest-impact knockdowns. But raw impact alone mostly \
   surfaces obvious TCR machinery (flagged is_obvious_tcr_machinery=true). That is \
   REPRODUCTION, not discovery. Novelty comes from druggable, disease-linked genes.
2. verify_candidate — for every gene you are considering, TRY TO REFUTE IT. A gene \
   that fails a GATE (donor/guide robustness, real knockdown, power) is a donor \
   artifact or noise — REJECT it and say so. Never promote an unverified gene.
3. check_open_targets — is a verified hit actually druggable and immune-disease-linked? \
   This is what separates an interesting gene from an actionable target.
4. check_clinical_trials — if a target has a known inhibitor, verify its trial status \
   live. Only claim "in the clinic" when the tool returns found=true with an NCT id.

Method: rank broadly, then scrutinize the handful that matter. Prefer genes that are \
druggable AND disease-linked AND survive verification over merely high-impact ones. \
Be honest: report rejected candidates and the number that killed them. Never assert a \
clinical phase you did not verify. Cite the computed value behind every claim.

Deliver: a ranked shortlist of promoted targets, each with its verdict, the key \
numbers (impact, donor/guide robustness, druggability, disease), any independent \
corroboration, and one line on the validation experiment. Plus the notable rejections."""


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
