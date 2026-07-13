"""CONCORD AGENT-INTENT GATE — the agent's tools must let the question choose the condition.

Regression tests for the intent/presentation coupling bug: the reconcile tool used to accept
only {gene} and always focus Stim48hr, so a question about a different condition (or a
time-course comparison) was silently answered at the wrong condition, and the frontend had no
condition to honour. This gate locks the FIX at the tool layer, where it is deterministic:

  1. condition propagation — reconcile_gene(gene, condition?) focuses the condition the question
     names, and still defaults to Stim48hr when none is given (backward compatible).
  2. compare_conditions — a dedicated cross-condition/time-course tool exists, returns the
     code-computed verdict per condition, and emits a `compare` view the UI can render.
  3. question-scoped tool selection — the tool surface and the prompt both offer the compare
     branch, so a time-course question has somewhere to go other than a single reconcile.

The true routing (which tool the agent PICKS) is prompt-driven and verified in the running app;
here we assert the deterministic proxy: the branch exists in the tool surface and the prompt.

House discipline (mirrors test_concord_sanity / test_tools):
  - drive async handlers with asyncio.run(); read the payload off content[0].text.
  - assert against the code-computed verdict, never a UI-rounded field.
  - the words-only `plain` summaries must NOT leak raw statistics (z / lfc / p / fdr).

Init `target_triage.core` before `target_triage.llm` to sidestep the pre-existing llm<->core
circular import that only bites when llm is imported first (the full suite dodges it via
collection order; a standalone run of THIS file must dodge it explicitly).

Run:  pytest eval/test_concord_agent_intent.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import target_triage.core  # noqa: E402,F401  (initialize core before llm — see module docstring)
from target_triage.llm import concord_prompt, concord_tools  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _view(result: dict) -> dict:
    return _payload(result)["__view_update__"]


# A gene present in all three conditions with a verdict that CHANGES across them, so a
# condition-blind answer is demonstrably wrong. TSC1: protein_only@Rest -> discordant@Stim48hr.
_GENE = "TSC1"

# canonical stat tokens that must never appear in a words-only narration payload.
_STAT_RE = re.compile(r"z\s*=|z-score|lfc|log[- ]?fold|p\s*=|p-value|q\s*=|fdr|adj[_ ]?p", re.I)


# ── 1. condition propagation ─────────────────────────────────────────────────────────────


def test_reconcile_defaults_to_stim48hr_when_no_condition_given():
    """Backward compatible: naming only a gene still focuses the canonical demo condition."""
    vu = _view(_run(concord_tools.reconcile_gene.handler({"gene": _GENE})))
    assert vu["action"] == "reconcile" and vu["gene"] == _GENE
    assert vu["condition"] == "Stim48hr", "no-condition reconcile must still default to Stim48hr"


def test_reconcile_honours_an_explicit_condition():
    """The reported bug: the tool ignored any requested condition and pinned Stim48hr. A question
    about rest must focus Rest, where TSC1's verdict is protein_only, not the Stim48hr discordant."""
    out = _run(concord_tools.reconcile_gene.handler({"gene": _GENE, "condition": "Rest"}))
    payload, vu = _payload(out), _view(out)
    assert vu["condition"] == "Rest", "reconcile_gene ignored the requested condition"
    assert payload["focus_condition"] == "Rest"
    # and the focused verdict is the code-computed one for THAT condition
    assert payload["by_condition"]["Rest"]["verdict"] == "protein_only"


def test_reconcile_condition_is_case_and_alias_insensitive():
    """An LLM may pass '48h' or 'resting'. Resolve loosely, but always land on a real condition."""
    for raw, want in [("rest", "Rest"), ("resting", "Rest"), ("48h", "Stim48hr"),
                      ("8 hours", "Stim8hr"), ("STIM48HR", "Stim48hr")]:
        vu = _view(_run(concord_tools.reconcile_gene.handler({"gene": _GENE, "condition": raw})))
        assert vu["condition"] == want, f"condition {raw!r} resolved to {vu['condition']}, want {want}"


def test_reconcile_unknown_condition_falls_back_not_crashes():
    """A condition the gene has no row for must degrade to a valid focus, never error or None."""
    vu = _view(_run(concord_tools.reconcile_gene.handler({"gene": _GENE, "condition": "nonsense"})))
    assert vu["condition"] in {"Rest", "Stim8hr", "Stim48hr"}


# ── 2. compare_conditions tool ───────────────────────────────────────────────────────────


def test_compare_conditions_tool_exists_and_is_allowed():
    assert hasattr(concord_tools, "compare_conditions"), "compare_conditions tool is missing"
    assert "mcp__concord__compare_conditions" in concord_tools.CONCORD_ALLOWED_TOOLS
    server_tools = {t.name for t in (
        concord_tools.reconcile_gene, concord_tools.gene_evidence,
        concord_tools.known_biology, concord_tools.compare_conditions)}
    assert server_tools == {"reconcile_gene", "gene_evidence", "known_biology", "compare_conditions"}


def test_compare_all_conditions_returns_each_code_verdict_in_order():
    """No conditions arg -> compare across all available, ordered Rest -> Stim8hr -> Stim48hr, each
    carrying the code-computed verdict. For TSC1 that trajectory is the whole point."""
    out = _run(concord_tools.compare_conditions.handler({"gene": _GENE}))
    payload, vu = _payload(out), _view(out)
    assert vu["action"] == "compare" and vu["gene"] == _GENE
    assert payload["ordered_conditions"] == ["Rest", "Stim8hr", "Stim48hr"]
    assert vu["conditions"] == ["Rest", "Stim8hr", "Stim48hr"]
    verdicts = {c: payload["by_condition"][c]["verdict"] for c in payload["ordered_conditions"]}
    assert verdicts == {"Rest": "protein_only", "Stim8hr": "discordant", "Stim48hr": "discordant"}


def test_compare_respects_a_requested_condition_subset():
    """Naming two conditions compares exactly those, in canonical order — not all three."""
    out = _run(concord_tools.compare_conditions.handler(
        {"gene": _GENE, "conditions": "Stim48hr, Rest"}))
    payload = _payload(out)
    assert payload["ordered_conditions"] == ["Rest", "Stim48hr"], "subset not honoured / not ordered"


def test_compare_unknown_gene_is_honest_not_fabricated():
    out = _run(concord_tools.compare_conditions.handler({"gene": "NOTAGENE"}))
    payload = _payload(out)
    assert payload.get("error"), "an unknown gene must return an honest error, not a fake trajectory"
    assert "__view_update__" not in payload, "no view should render for an unresolvable gene"


def test_compare_payload_is_words_only_no_leaked_statistics():
    """The narration surface (by_condition[*].plain) must stay words-only, same as reconcile."""
    payload = _payload(_run(concord_tools.compare_conditions.handler({"gene": _GENE})))
    for cond, block in payload["by_condition"].items():
        blob = json.dumps(block["plain"])
        assert not _STAT_RE.search(blob), f"{cond} plain summary leaked a raw statistic: {blob}"


# ── 3. question-scoped tool selection (prompt proxy) ─────────────────────────────────────


def test_prompt_routes_time_course_questions_to_compare():
    """The decision tree must give a cross-condition / time-course question its own branch, or the
    agent will fall back to a single-condition reconcile at the wrong condition (the reported bug)."""
    p = concord_prompt.CONCORD_SYSTEM_PROMPT.lower()
    assert "compare_conditions" in p, "prompt never mentions the compare tool — agent can't route to it"
    assert any(k in p for k in ("time-course", "time course", "across conditions", "between",
                                "over time", "trajectory")), \
        "prompt has no cross-condition / time-course trigger language"


def test_prompt_lets_reconcile_take_a_named_condition():
    p = concord_prompt.CONCORD_SYSTEM_PROMPT.lower()
    assert "condition" in p, "prompt never tells the agent it can pass a condition to reconcile_gene"


# ── 4. decision-brief tool (the 'what should I do' path) ─────────────────────────────────


def test_draft_decision_brief_emits_a_plan_view_with_the_whole_brief():
    """'What should I do about TSC1' must reach the decision brief, not a bare reconcile. The tool
    ships the whole brief in the view_update so the browser renders without a second fetch."""
    out = _run(concord_tools.draft_decision_brief.handler({"gene": _GENE}))
    vu = _view(out)
    assert vu["action"] == "plan" and vu["gene"] == _GENE
    b = vu["decision_brief"]
    assert b["snapshot"]["verdict"] == "discordant"          # code-computed, carried through
    assert b["comparability"] == "partially_comparable"
    assert b["citations"] and all(c["supports"] == "mtor_background" for c in b["citations"])


def test_draft_decision_brief_never_fabricates_for_an_unknown_gene():
    out = _run(concord_tools.draft_decision_brief.handler({"gene": "NOTAGENE12345"}))
    payload = _payload(out)
    assert "__view_update__" not in payload, "an unknown gene must not produce a plan view"
    assert payload.get("error"), "an unknown gene must return an honest error"


def test_draft_decision_brief_is_registered_and_prompt_routes_to_it():
    assert "mcp__concord__draft_decision_brief" in concord_tools.CONCORD_ALLOWED_TOOLS
    p = concord_prompt.CONCORD_SYSTEM_PROMPT.lower()
    assert "draft_decision_brief" in p, "prompt never mentions the decision-brief tool"
    assert any(k in p for k in ("what should i do", "validation", "resolve the disagreement",
                                "what experiment", "next step")), \
        "prompt has no decision-brief trigger language"


# ── 5. discovery: rank_targets surfaces the code-ranked candidate shortlist ──────────────────
# The dogfooding gap: a scientist asking "find new targets" was told Concord can't, even though
# compute_shortlist ranks the whole screen. rank_targets is the front door — these lock the control
# that the tool serves ONLY the code-ranked closed set (agent never authors a gene or a rank).


def test_rank_targets_returns_code_ranked_closed_set():
    out = _run(concord_tools.rank_targets.handler({"limit": 6}))
    payload, vu = _payload(out), _view(out)
    assert vu["action"] == "rank_targets"
    cands = vu["candidates"]
    assert len(cands) == 6, "rank_targets must honour the requested limit"
    # ranks are the code's, contiguous from 1 (the agent cannot reorder or invent a rank)
    assert [c["rank"] for c in cands] == [1, 2, 3, 4, 5, 6]
    # every surfaced gene is a real screen gene, never fabricated
    for c in cands:
        assert c["gene"] in concord_tools._GENES, f"{c['gene']} is not a real screen gene"
    assert payload["plain"]["n_total_significant"] > len(cands)


def test_rank_targets_leaks_no_raw_statistic_to_the_agent():
    """Same words-only discipline as every other tool: druggability/disease are TIER WORDS, and no
    z/lfc/p/fdr or raw score reaches the narration payload — the numbers live in the card only."""
    out = _run(concord_tools.rank_targets.handler({}))
    payload = _payload(out)
    # the agent-facing text carries no stat tokens or raw score field names
    text = json.dumps({k: v for k, v in payload.items() if k != "__view_update__"})
    assert not _STAT_RE.search(text), "rank_targets narration payload leaks a statistic"
    for score_key in ("druggable_score", "disease_score", "actionable_score"):
        assert score_key not in text, f"raw {score_key} must not reach the agent payload"


def test_rank_targets_limit_is_clamped_not_crashed():
    """A silly limit degrades to the bounds, never errors."""
    assert len(_view(_run(concord_tools.rank_targets.handler({"limit": 9999})))["candidates"]) \
        <= concord_tools._RANK_MAX_N
    assert len(_view(_run(concord_tools.rank_targets.handler({"limit": 0})))["candidates"]) >= 1


def test_rank_targets_is_registered_and_prompt_routes_discovery_to_it():
    assert "mcp__concord__rank_targets" in concord_tools.CONCORD_ALLOWED_TOOLS
    assert concord_tools.rank_targets.name == "rank_targets"
    p = concord_prompt.CONCORD_SYSTEM_PROMPT.lower()
    assert "rank_targets" in p, "prompt never mentions the discovery tool"
    assert any(k in p for k in ("find new drug targets", "surface candidate", "top candidates",
                                "does not yet have a gene", "rank the screen")), \
        "prompt has no discovery trigger language"
    # the prompt must NOT still claim Concord can't find targets
    assert "concord can" in p and "front door" in p, \
        "prompt should affirm Concord CAN surface candidates"


# ── 6. sketch → pathway map for curated genes ────────────────────────────────────────────
# "sketch/draw GENE" should earn the richer signalling cascade when the gene is in a curated
# pathway, and fall back to the bench cartoon otherwise. Regression lock for the routing fix:
# the live app was showing the weak old sketch for genes that now have a full pathway figure.

def test_sketch_of_a_curated_gene_emits_the_pathway_map_figure():
    """A curated gene (ITK sits in TCR signalling) asked to be sketched must render the CST-style
    pathway cascade, not the bench cartoon — same `pathway_map` action the frontend already draws."""
    vu = _view(_run(concord_tools.sketch_gene.handler({"gene": "ITK"})))
    assert vu["action"] == "pathway_map", "a curated gene's sketch must upgrade to the pathway map"
    assert vu["gene"] == "ITK"
    assert vu["pathway_map"]["style"] == "topology"          # the real cascade, not the starburst


def test_sketch_of_a_non_curated_gene_stays_the_bench_cartoon():
    """A gene with no curated wiring (TSC1) keeps the honest bench sketch — the fallback is intact."""
    vu = _view(_run(concord_tools.sketch_gene.handler({"gene": _GENE})))
    assert vu["action"] == "sketch", "a non-curated gene must keep the bench sketch"
    assert vu["gene"] == _GENE


def test_sketch_routing_honours_the_requested_condition():
    """The condition the question names still propagates through the sketch→pathway_map upgrade."""
    out = _run(concord_tools.sketch_gene.handler({"gene": "ITK", "condition": "Stim8hr"}))
    assert _view(out)["action"] == "pathway_map"
    assert _payload(out)["condition"] == "Stim8hr"           # the named condition propagated
