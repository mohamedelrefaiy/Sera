"""Concord agent tools: expose the mRNA×protein reconciliation to Claude via the Agent SDK.

The Concord chat is a REAL reasoning agent, not a fixed router: it reads the message, decides
whether to reconcile a gene, pull its evidence, or just answer, and calls the matching tool. Each
@tool is a thin wrapper around the DETERMINISTIC concordance artifact — the tool returns the
code-computed verdict and the words-only `plain` summary; the agent NARRATES, it never recomputes
or overrides the verdict (the "agents on the rim" invariant, see docs/architecture/CONCORD_AGENT_BUILD.md).

Data is loaded once at import into immutable module state (the artifact is static), so repeated tool
calls are cheap. Gene arguments are validated against the concordance table — a symbol not in the
screens returns an honest "not in the screens", never a fabricated verdict (the same closed-set gate
the router used).
"""
from __future__ import annotations

import json
import os
import re

from claude_agent_sdk import create_sdk_mcp_server, tool

from ..core.explanation import build_record

# --- load-once immutable state -------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ARTIFACTS = os.path.join(os.path.dirname(_HERE), "data", "artifacts")
_CONCORDANCE_PARQUET = os.path.join(_ARTIFACTS, "concordance.parquet")
_ENRICHMENT_JSON = os.path.join(_ARTIFACTS, "enrichment.json")
_GROUND_TRUTH_JSON = os.path.join(_ARTIFACTS, "ground_truth.json")
_PROVENANCE_JSON = os.path.join(_ARTIFACTS, "provenance.json")


def _load_concordance() -> list[dict]:
    """Read the concordance artifact into JSON-able dicts, or [] if absent. NaN -> None so the
    payload is valid JSON. Mirrors api/app.py's loader so the agent sees the SAME rows the
    deterministic endpoints serve."""
    if not os.path.exists(_CONCORDANCE_PARQUET):
        return []
    import math

    import pandas as pd

    df = pd.read_parquet(_CONCORDANCE_PARQUET)
    clean: list[dict] = []
    for r in df.to_dict("records"):
        row = {}
        for k, v in r.items():
            if v is None or (isinstance(v, float) and math.isnan(v)):
                row[k] = None
            elif hasattr(v, "item"):
                row[k] = v.item()
            else:
                row[k] = v
        clean.append(row)
    return clean


def _load_enrichment() -> dict[str, dict]:
    if not os.path.exists(_ENRICHMENT_JSON):
        return {}
    with open(_ENRICHMENT_JSON) as fh:
        return json.load(fh)


def _load_ground_truth() -> dict:
    if not os.path.exists(_GROUND_TRUTH_JSON):
        return {}
    with open(_GROUND_TRUTH_JSON) as fh:
        return json.load(fh)


def _load_provenance() -> dict:
    if not os.path.exists(_PROVENANCE_JSON):
        return {}
    with open(_PROVENANCE_JSON) as fh:
        return json.load(fh)


_CONCORDANCE = _load_concordance()
_ENRICHMENT = _load_enrichment()
_GROUND_TRUTH = _load_ground_truth()
_PROVENANCE = _load_provenance()
_GENES = {r["gene"] for r in _CONCORDANCE}          # the ONLY genes a tool may resolve
_BY_GENE: dict[str, list[dict]] = {}
for _r in _CONCORDANCE:
    _BY_GENE.setdefault(_r["gene"], []).append(_r)


def _text(payload) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


def _view(agent_facts: dict, view_update: dict) -> dict:
    """A tool result that ALSO carries a UI action. The API extracts `__view_update__` from the
    tool-result text and forwards it to the browser, so the agent's tool call IS the UI action —
    same protocol as llm/tools.py."""
    payload = {**agent_facts, "__view_update__": view_update}
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


# --- condition resolution ------------------------------------------------------------------
# The three activation conditions in the screens, in their natural time order. A cross-condition
# view (compare_conditions) and any "default focus" must present them in THIS order, never the
# dict-insertion order of the loaded rows.
_CANON_ORDER = ("Rest", "Stim8hr", "Stim48hr")

# Loose aliases an LLM (or a scientist) might type for each condition. Keys are the normalised
# token (lowercased, whitespace/dashes/underscores stripped); values are the canonical name.
_COND_ALIASES = {
    "rest": "Rest", "resting": "Rest", "unstim": "Rest", "unstimulated": "Rest",
    "baseline": "Rest", "0h": "Rest", "0hr": "Rest", "t0": "Rest",
    "stim8hr": "Stim8hr", "stim8h": "Stim8hr", "8h": "Stim8hr", "8hr": "Stim8hr",
    "8hour": "Stim8hr", "8hours": "Stim8hr", "early": "Stim8hr",
    "stim48hr": "Stim48hr", "stim48h": "Stim48hr", "48h": "Stim48hr", "48hr": "Stim48hr",
    "48hour": "Stim48hr", "48hours": "Stim48hr", "stim": "Stim48hr", "stimulated": "Stim48hr",
    "late": "Stim48hr",
}


def _norm_cond_token(raw: str) -> str:
    """Lowercase and strip whitespace/dashes/underscores so '48 h' and 'Stim48hr' compare cleanly."""
    return re.sub(r"[\s_\-]+", "", (raw or "").strip().lower())


def _resolve_condition(raw, available: list[str]) -> str | None:
    """Resolve a loosely-typed condition to a canonical name the gene ACTUALLY has, or None.

    A name is only accepted if it maps to a condition present in `available` — the same closed-set
    discipline as gene resolution. Matches exact canonical names case-insensitively first, then the
    small alias table (rest / 8h / 48h / …). Never guesses a condition the gene lacks.
    """
    if not raw:
        return None
    tok = _norm_cond_token(raw)
    for c in available:                       # exact canonical, case-insensitive
        if _norm_cond_token(c) == tok:
            return c
    canon = _COND_ALIASES.get(tok)            # then the alias table
    return canon if canon in available else None


def _order_conditions(conds) -> list[str]:
    """Canonical Rest -> Stim8hr -> Stim48hr order; any unexpected extras appended stably."""
    conds = list(conds)
    known = [c for c in _CANON_ORDER if c in conds]
    extra = [c for c in conds if c not in _CANON_ORDER]
    return known + extra


def _parse_conditions_arg(raw) -> list[str]:
    """Split a comma / space / semicolon separated conditions string into raw tokens (unresolved)."""
    if not raw:
        return []
    return [t for t in re.split(r"[,;\s]+", str(raw).strip()) if t]


def _by_condition(hits: list[dict], gene: str) -> dict[str, dict]:
    """Per-condition {verdict, plain} for a gene, reusing the experimentalist-voice record builder.
    The verdict is the value the CODE computed; `plain` is words-only (no z / lfc / p / fdr)."""
    out = {}
    for r in hits:
        rec = build_record(r, gene)
        out[r["condition"]] = {"verdict": r["verdict"], "plain": rec["plain"]}
    return out


@tool(
    "reconcile_gene",
    "Reconcile ONE gene's mRNA (Perturb-seq) vs protein (FACS) CRISPR screens into the "
    "code-computed concordance verdict (replicated / discordant / mRNA-only / protein-only / "
    "neither) AT ONE activation condition. Returns the verdict plus a WORDS-ONLY summary "
    "(transcript up/down, protein up/down, strength) you narrate from — never quote the raw "
    "statistics. Pass `condition` (Rest, Stim8hr, or Stim48hr) to focus the condition the "
    "question is about; omit it to default to Stim48hr. For how a gene CHANGES across conditions "
    "or over time, call `compare_conditions` instead — do NOT call this three times.",
    {
        "type": "object",
        "properties": {
            "gene": {"type": "string", "description": "Gene symbol, e.g. TSC1."},
            "condition": {
                "type": "string",
                "description": "Optional activation condition to focus: Rest, Stim8hr, or "
                               "Stim48hr. Omit to default to Stim48hr.",
            },
        },
        "required": ["gene"],
    },
)
async def reconcile_gene(args):
    gene = (args.get("gene") or "").strip().upper()
    hits = _BY_GENE.get(gene)
    if not hits:
        return _text({"gene": gene, "error": "not in the screens",
                      "note": "Only genes present in both CRISPR screens can be reconciled."})
    by_condition = _by_condition(hits, gene)
    available = list(by_condition.keys())
    # The question chooses the condition; fall back to the canonical demo condition (Stim48hr),
    # then to the earliest available. A requested-but-unavailable condition degrades, never errors.
    requested = _resolve_condition(args.get("condition"), available)
    focus_cond = requested or ("Stim48hr" if "Stim48hr" in by_condition
                               else _order_conditions(available)[0])
    return _view(
        {"gene": gene, "cytokine": hits[0]["cytokine"], "by_condition": by_condition,
         "focus_condition": focus_cond,
         "note": "Verdict computed by code. Narrate the `plain` summary in plain language — do NOT "
                 "quote z-scores, log-fold-changes, p-values or FDRs; those live in the figure."},
        {"action": "reconcile", "gene": gene, "condition": focus_cond},
    )


@tool(
    "compare_conditions",
    "Compare ONE gene's concordance verdict ACROSS activation conditions — the time-course / "
    "cross-condition view. Use this for 'how does GENE change between rest and 48h', 'over time', "
    "'across conditions', or when the user names two or more conditions. Returns the code-computed "
    "verdict and a WORDS-ONLY summary for EACH condition, in time order (Rest -> Stim8hr -> "
    "Stim48hr); you narrate the trajectory (e.g. what shifts from rest to late stimulation). Pass "
    "`conditions` to restrict to a subset (comma-separated); omit it to compare all the gene has. "
    "Never quote raw statistics.",
    {
        "type": "object",
        "properties": {
            "gene": {"type": "string", "description": "Gene symbol, e.g. TSC1."},
            "conditions": {
                "type": "string",
                "description": "Optional comma-separated subset of Rest, Stim8hr, Stim48hr "
                               "(e.g. 'Rest, Stim48hr'). Omit to compare every condition the "
                               "gene has.",
            },
        },
        "required": ["gene"],
    },
)
async def compare_conditions(args):
    gene = (args.get("gene") or "").strip().upper()
    hits = _BY_GENE.get(gene)
    if not hits:
        return _text({"gene": gene, "error": "not in the screens",
                      "note": "Only genes present in both CRISPR screens can be compared."})
    all_by_condition = _by_condition(hits, gene)
    available = list(all_by_condition.keys())
    # Requested subset (resolved + validated against what the gene actually has), else everything.
    requested_raw = _parse_conditions_arg(args.get("conditions"))
    if requested_raw:
        resolved = [c for c in (_resolve_condition(r, available) for r in requested_raw) if c]
        chosen = list(dict.fromkeys(resolved)) or available   # dedupe; empty -> compare all
    else:
        chosen = available
    ordered = _order_conditions(chosen)
    by_condition = {c: all_by_condition[c] for c in ordered}
    return _view(
        {"gene": gene, "cytokine": hits[0]["cytokine"], "ordered_conditions": ordered,
         "by_condition": by_condition,
         "note": "Verdict per condition computed by code, in time order. Narrate the TRAJECTORY in "
                 "plain language — what changes from rest to stimulation and why it matters — from "
                 "the `plain` summaries. Do NOT quote z-scores, log-fold-changes, p-values or FDRs."},
        {"action": "compare", "gene": gene, "conditions": ordered},
    )


@tool(
    "gene_evidence",
    "Pull the actionability dossier for a gene: druggability (small-molecule AND antibody "
    "modality, tracked separately), best immune-disease genetic association, and the QC confidence "
    "tier. Use when the user asks whether a gene is a viable drug target, its disease links, or how "
    "reliable the hit is. Independent of the verdict — use it to judge whether a hit is worth chasing.",
    {"gene": str},
)
async def gene_evidence(args):
    gene = (args.get("gene") or "").strip().upper()
    if gene not in _GENES:
        return _text({"gene": gene, "error": "not in the screens"})
    en = _ENRICHMENT.get(gene)
    if not en:
        return _text({"gene": gene, "enrichment": None,
                      "note": "No enrichment record for this gene — say the dossier is unavailable, "
                              "do not invent druggability or disease links."})
    return _view({"gene": gene, "enrichment": en},
                 {"action": "evidence", "gene": gene})


@tool(
    "known_biology",
    "Answer whether Concord recovers KNOWN IL-2 biology — the validation question, corpus-wide (not "
    "one gene). Returns how many canonical IL-2 regulators the two screens recover, how many are "
    "protein-only (detected only by the protein screen — the protein-level hits a transcriptome-only "
    "search misses, for which post-transcriptional regulation is the leading hypothesis), how the "
    "known brake TSC1 lands, and the point that the aggregate correlation is ~zero and hides this "
    "structure. Call this for 'does it recover known biology?', 'is it validated?', 'does it work?'.",
    {},
)
async def known_biology(args):
    gt = _GROUND_TRUTH
    if not gt:
        return _text({"error": "ground-truth artifact not built",
                      "note": "Say the validation panel isn't available; do not invent recovery numbers."})
    s = gt.get("summary", {})
    n_pos = s.get("n_positive"); n_rec = s.get("n_recovered")
    n_po = s.get("n_protein_only"); n_rep = s.get("n_replicated")
    # WORDS the agent narrates from — no z/lfc here. The panel (view_update) shows the numbers.
    plain = {
        "recovered_summary": f"{n_rec} of {n_pos} canonical IL-2 positive regulators show up as hits",
        "protein_only_finding": (
            f"{n_po} of those are protein-only — detected only by the protein screen, the "
            "protein-level hits a transcript-only screen would miss entirely; post-transcriptional "
            "regulation is the leading hypothesis for that gap (this is the payoff)"),
        "replicated_count": f"{n_rep} are replicated (both screens agree)",
        "known_brake": (
            "the canonical brake TSC1 comes out discordant — it lowers the transcript but raises the "
            "protein, so a transcript-only read would have called it backwards"),
        "aggregate_point": (
            "across all shared genes the two screens correlate at essentially zero, so a naive "
            "average would hide every one of these hits — the structure only appears gene by gene"),
        "takeaway": "the recovery of the protein-only regulators is the evidence Concord works",
    }
    return _view(
        {"plain": plain,
         "note": "Narrate from `plain` in plain language for a bench scientist. Do NOT quote the "
                 "z-scores, log-fold-changes, or the raw correlation number; the panel shows those."},
        {"action": "known_biology"},
    )


_BRIEF_ANCHOR_ORDER = ("Stim48hr", "Stim8hr", "Rest")


@tool(
    "draft_decision_brief",
    "Answer the SCIENTIFIC-DECISION question for ONE gene: given the screen disagreement, what do "
    "we still not know, and which feasible experiment resolves it? Call this for 'what should I do "
    "about GENE', 'draft/design a validation plan', 'how do I resolve this', 'what experiment', "
    "'next steps for GENE'. Returns the deterministic decision brief — the code-computed verdict, a "
    "comparability audit, competing explanations (a closed set, not invented), one discriminating "
    "experiment with its expected-outcome matrix, a stop/go decision, and background citations. You "
    "narrate a one-line lead-in; the brief itself renders as a structured card. Do NOT restate the "
    "brief's contents or quote raw statistics.",
    {
        "type": "object",
        "properties": {
            "gene": {"type": "string", "description": "Gene symbol, e.g. TSC1."},
        },
        "required": ["gene"],
    },
)
async def draft_decision_brief(args):
    import dataclasses

    from ..core.brief_resolver import (
        resolve_claims, resolve_context, resolve_dossier, resolve_snapshot)
    from ..core.decision_brief import build_decision_brief

    gene = (args.get("gene") or "").strip().upper()
    hits = _BY_GENE.get(gene)
    if not hits:
        return _text({"gene": gene, "error": "not in the screens",
                      "note": "Only genes present in both CRISPR screens can be reconciled."})
    by_cond = {r["condition"]: r for r in hits}
    anchor = next((c for c in _BRIEF_ANCHOR_ORDER if c in by_cond), hits[0]["condition"])
    row = by_cond[anchor]
    try:
        snapshot = resolve_snapshot(row, _PROVENANCE)
        context = resolve_context(row, _PROVENANCE)
        claims = resolve_claims(row["gene"], row["cytokine"], anchor, _PROVENANCE)
        dossier = resolve_dossier(_ENRICHMENT.get(gene))
        brief = dataclasses.asdict(
            build_decision_brief(snapshot, context, claims, dossier=dossier))
    except (ValueError, KeyError, AssertionError) as e:
        return _text({"gene": gene, "error": "could not build a decision brief",
                      "detail": str(e),
                      "note": "Say the decision brief isn't available for this gene; do not invent one."})
    # The brief renders as a card; the agent adds a one-line lead-in. Ship the whole brief in the
    # view_update so the browser renders without a second fetch. Verdict and advancement stance are
    # both code-computed — the agent may reflect the stance in words but never overrides it.
    return _view(
        {"gene": gene, "verdict": brief["snapshot"]["verdict"],
         "comparability": brief["comparability"], "feasible": brief["feasible"],
         "recommendation": brief["recommendation"],
         "note": "One plain lead-in sentence only. Reflect the code-computed advancement stance in "
                 "plain words (advance / validate first / hold as a weak target / deprioritise) — "
                 "e.g. 'Real biology, but a weak drug target — here's how I'd resolve the " + gene
                 + " split.'. The card carries the detail — do NOT restate the experiment, "
                 "explanations, dossier scores, or numbers."},
        {"action": "plan", "gene": gene, "decision_brief": brief},
    )


def build_concord_server():
    """The in-process MCP server hosting Concord's tools."""
    return create_sdk_mcp_server(
        name="concord",
        version="0.1.0",
        tools=[reconcile_gene, compare_conditions, gene_evidence, known_biology,
               draft_decision_brief],
    )


CONCORD_ALLOWED_TOOLS = [
    "mcp__concord__reconcile_gene",
    "mcp__concord__compare_conditions",
    "mcp__concord__gene_evidence",
    "mcp__concord__known_biology",
    "mcp__concord__draft_decision_brief",
]
