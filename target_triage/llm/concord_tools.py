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
from ..core.pathway_topology import CURATED_PATHWAYS as _CURATED_PATHWAYS

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


@tool(
    "hitlist_biology",
    "Answer what BIOLOGY the replicated hits share — corpus-wide, not one gene. Concord's replicated "
    "set (genes both screens agree on) is run through pathway enrichment; this returns the shared "
    "pathways (e.g. 'TCR Signaling') the hits cluster in, with a plain summary. Call this for 'what "
    "do the hits have in common', 'what pathways are enriched', 'shared biology', 'what connects "
    "these genes', or a question about the hit list as a set. The hit list and the pathways are "
    "computed by CODE (verdict-selected genes -> Reactome enrichment); you narrate the shared theme "
    "in one or two plain sentences and NEVER name a pathway that isn't in the returned set.",
    {},
)
async def hitlist_biology(args):
    import dataclasses

    from ..clients import enrichr
    from ..core.hitlist_enrichment import build_hitlist_enrichment

    if not _CONCORDANCE:
        return _text({"error": "concordance artifact not built",
                      "note": "Say the hit-list biology isn't available; do not invent pathways."})
    result = build_hitlist_enrichment(_CONCORDANCE, enrichr.enrich)
    payload = dataclasses.asdict(result)
    if not result.pathways:
        return _text({"hitlist": payload,
                      "note": "No enriched pathways came back. Say so plainly — do NOT invent a "
                              "shared theme or name a pathway."})
    return _view(
        {"hitlist": payload,
         "note": "Narrate the shared biology in ONE or TWO plain sentences from `plain_summary` / "
                 "`top_term` — name only pathways present in `pathways`, never one you recall. Do "
                 "NOT quote the q-values; the panel shows them."},
        {"action": "hitlist_biology", "hitlist": payload},
    )


# --- discovery: rank the whole screen for candidate targets -------------------------------------
# The shortlist is expensive (ranks + verifies every significant gene, hits the OT cache). Compute
# it once, lazily, and hold it in module state — the screen is static, so the ranking never changes
# within a process. Mirrors api/app.py's _SHORTLISTS cache; the tool serves the SAME rows the
# deterministic /api/shortlist endpoint does, so chat discovery and the Hit-list view never diverge.
_SHORTLIST_CACHE: list[dict] | None = None


def _shortlist_rows() -> list[dict]:
    """The ranked + verified + annotated shortlist for the Marson screen, computed once."""
    global _SHORTLIST_CACHE
    if _SHORTLIST_CACHE is None:
        from ..core.shortlist import compute_shortlist
        _SHORTLIST_CACHE = compute_shortlist()
    return _SHORTLIST_CACHE


#: Druggability tier words — the agent narrates a TIER, never the raw score (same words-not-figures
#: discipline as every other tool). Closed set; the code picks the bucket, the agent reads it out.
def _drug_tier(score: float) -> str:
    if score >= 0.6:
        return "strong drug handle"
    if score >= 0.3:
        return "some drug handle"
    return "little drug handle"


def _disease_tier(score: float) -> str:
    if score >= 0.7:
        return "strong disease genetics"
    if score >= 0.4:
        return "moderate disease genetics"
    return "weak disease genetics"


#: The default size of the surfaced candidate list. Small on purpose — a triage shortlist a
#: scientist can actually read and act on, not a genome-scale dump.
_RANK_DEFAULT_N = 12
_RANK_MAX_N = 25


@tool(
    "rank_targets",
    "Surface CANDIDATE drug targets from the whole screen — the DISCOVERY question ('find new "
    "targets', 'what should I look at', 'top hits', 'rank the screen', 'which genes are worth "
    "chasing'). Returns the code-computed shortlist: every significant gene ranked by an actionable "
    "score (knockdown impact reweighted by druggability x disease genetics, with obvious TCR "
    "machinery damped so novel candidates float up), each already verified and carrying its "
    "concordance verdict. This is the front door for a scientist who does NOT yet have a gene in "
    "mind. The ranking, the genes, and their verdicts are ALL computed by code — you narrate the "
    "shape of the list (how many strong candidates, what stands out) in plain words and NEVER add a "
    "gene, invent a rank, or quote the raw scores. Point the scientist at reconcile_gene or "
    "draft_decision_brief to go deeper on any one. Pass `limit` to widen/narrow (default 12).",
    {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "How many top candidates to surface (default 12, max 25).",
            },
        },
    },
)
async def rank_targets(args):
    rows = _shortlist_rows()
    if not rows:
        return _text({"error": "shortlist not available",
                      "note": "Say the ranked candidate list isn't available; do not invent genes "
                              "or ranks."})
    try:
        n = int(args.get("limit") or _RANK_DEFAULT_N)
    except (TypeError, ValueError):
        n = _RANK_DEFAULT_N
    n = max(1, min(n, _RANK_MAX_N))

    top = rows[:n]
    # Words-only facts the agent narrates from — no raw scores. Each candidate is a CLOSED-SET row
    # the code produced; the agent may describe the list's shape but never add or reorder a gene.
    candidates = [
        {
            "rank": r["rank"],
            "gene": r["gene"],
            "verdict": r["verdict"],
            "best_condition": r["best_condition"],
            "druggability": _drug_tier(r.get("druggable_score") or 0.0),
            "disease": _disease_tier(r.get("disease_score") or 0.0),
            "top_disease": r.get("top_disease"),
            "clinical_stage": r.get("clinical_stage"),
        }
        for r in top
    ]
    n_promote = sum(1 for r in top if str(r["verdict"]).startswith("PROMOTE"))
    plain = {
        "n_shown": len(candidates),
        "n_total_significant": len(rows),
        "n_promote": n_promote,
        "lead_gene": candidates[0]["gene"] if candidates else None,
        "how_ranked": (
            "ranked by an actionable score — knockdown impact reweighted by druggability and immune-"
            "disease genetics, with obvious T-cell-receptor machinery damped so less-expected "
            "candidates rise"),
        "takeaway": (
            "these are the code-ranked candidates worth triaging; open any one to see whether the "
            "mRNA and protein screens agree and what to do next"),
    }
    return _view(
        {"plain": plain, "candidates": candidates,
         "note": "Narrate the SHAPE of this list in one or two plain sentences from `plain` — how "
                 "many strong candidates, what leads, how it was ranked. Do NOT list every gene in "
                 "prose (the card shows them), do NOT quote scores, and NEVER name a gene that is "
                 "not in `candidates`. Invite the scientist to open one with reconcile_gene."},
        {"action": "rank_targets", "candidates": candidates},
    )


_SKETCH_ANCHOR_ORDER = ("Stim48hr", "Stim8hr", "Rest")


@tool(
    "sketch_gene",
    "Draw a FIGURE for ONE gene. Call this for 'sketch GENE', 'draw GENE', 'show me a "
    "diagram/cartoon of GENE', 'visualise GENE', or when the user wants to SEE the mechanism rather "
    "than read it. If the gene sits in a curated signalling pathway (TCR, BCR, MAPK/ERK), this draws "
    "the full compartment cascade — membrane receptor → cytoplasmic cascade → nucleus → cytokine "
    "output — exactly like the pathway_map figure. Otherwise it draws the bench-notebook cartoon: "
    "knock out the gene, follow the transcript arrow and the protein arrow to the cytokine, with the "
    "two layers diverging when they disagree. Every node and arrow is CODE-derived (never invented); "
    "it renders as a figure. Give ONE plain lead-in sentence; do NOT restate the figure or quote "
    "numbers. Pass `condition` (Rest, Stim8hr, Stim48hr) to focus a condition; omit to default.",
    {
        "type": "object",
        "properties": {
            "gene": {"type": "string", "description": "Gene symbol, e.g. TSC1."},
            "condition": {
                "type": "string",
                "description": "Optional activation condition: Rest, Stim8hr, or Stim48hr.",
            },
        },
        "required": ["gene"],
    },
)
async def sketch_gene(args):
    import dataclasses

    from ..core.gene_sketch import build_gene_sketch
    from ..core.pathway_topology import select_for

    gene = (args.get("gene") or "").strip().upper()
    hits = _BY_GENE.get(gene)
    if not hits:
        return _text({"gene": gene, "error": "not in the screens",
                      "note": "Only genes present in both CRISPR screens can be sketched."})
    by_cond = {r["condition"]: r for r in hits}
    requested = _resolve_condition(args.get("condition"), list(by_cond.keys()))
    anchor = requested or next((c for c in _SKETCH_ANCHOR_ORDER if c in by_cond),
                               hits[0]["condition"])

    # When the gene sits in a CURATED pathway, "sketch it" earns the richer figure: draw the
    # signalling cascade (membrane → cytoplasm → nucleus → output) rather than the bench cartoon.
    # The bench sketch stays the honest fallback for a gene with no curated wiring. Same code-owned,
    # never-invented contract either way.
    if select_for(gene) is not None:
        return _pathway_map_view(gene, by_cond, anchor)

    try:
        sketch = dataclasses.asdict(build_gene_sketch(by_cond[anchor]))
    except (ValueError, KeyError) as e:
        return _text({"gene": gene, "error": "could not build a sketch", "detail": str(e),
                      "note": "Say the sketch isn't available for this gene; do not invent one."})
    return _view(
        {"gene": gene, "verdict": sketch["verdict"], "condition": anchor,
         "note": "ONE plain lead-in sentence only (e.g. 'Here's how " + gene + " reads at a "
                 "glance.'). The sketch figure carries the detail — do NOT restate the arrows or "
                 "quote numbers."},
        {"action": "sketch", "gene": gene, "sketch": sketch},
    )


# The code-owned replicated hit set (genes both screens agree on) — the enrichment universe the
# pathway map places a gene within. Computed here (not imported private) so the dependency is clean.
_REPLICATED_GENES = tuple(sorted({
    (r.get("gene") or "").strip().upper()
    for r in _CONCORDANCE if r.get("verdict") == "replicated" and (r.get("gene") or "").strip()}))

# every gene named in ANY curated topology, so we can shade the CST figure by hit-status.
_CURATED_NODE_IDS = frozenset(
    n.id for pw in _CURATED_PATHWAYS for n in pw.nodes)


def _curated_node_status(condition: str) -> dict[str, str]:
    """Hit-status for each curated-topology node at `condition`: 'hit' if it is a confident hit in
    either screen there, else 'context'. Read straight from the concordance table, never inferred; a
    curated node absent from the screens (e.g. the second-messenger IP3 or the output IL2) is simply
    omitted, so the renderer draws it solid. This is code-owned truth, like the verdict itself."""
    status: dict[str, str] = {}
    for gid in _CURATED_NODE_IDS:
        rows = _BY_GENE.get(gid)
        if not rows:
            continue
        row = next((r for r in rows if r.get("condition") == condition), rows[0])
        is_hit = bool(row.get("hit_rna")) or bool(row.get("hit_prot"))
        status[gid] = "hit" if is_hit else "context"
    return status


def _pathway_map_view(gene: str, by_cond: dict, anchor: str):
    """Build the pathway-map view for a resolved gene/condition. Shared by the `pathway_map` tool and
    by `sketch_gene`'s curated-gene routing, so both emit the identical `pathway_map` action the
    frontend renders. Pure of tool wrapping — a plain function both call at runtime."""
    import dataclasses

    from ..clients import enrichr
    from ..core.pathway_map import build_pathway_map

    # Enrichment over the replicated hit set gives real pathways WITH their overlap members, so the
    # STARBURST fallback places the gene among genuine partners. Cached (offline for the demo set).
    pathways = enrichr.enrich(_REPLICATED_GENES) if _REPLICATED_GENES else []
    # For the CURATED topology figure, shade each curated node by whether it is a confident hit in
    # THESE screens (solid) or surrounding context (faded). Read straight from the concordance table
    # at the same anchor condition — never inferred. A curated gene absent from the screens stays
    # unshaded (solid). This is a second axis of truth the starburst never had.
    node_status = _curated_node_status(anchor)
    try:
        pmap = dataclasses.asdict(
            build_pathway_map(by_cond[anchor], pathways, node_status=node_status))
    except (ValueError, KeyError) as e:
        return _text({"gene": gene, "error": "could not build a pathway map", "detail": str(e),
                      "note": "Say the pathway map isn't available for this gene; do not invent one."})
    return _view(
        {"gene": gene, "verdict": pmap["focal_verdict"], "pathway": pmap["pathway"],
         "condition": anchor,
         "note": "ONE plain lead-in sentence only (e.g. 'Here's where " + gene + " sits.'). The map "
                 "carries the partners and hypotheses — do NOT list the partners or name a pathway "
                 "the map didn't return; hypotheses are hypotheses, never state them as fact."},
        {"action": "pathway_map", "gene": gene, "pathway_map": pmap},
    )


@tool(
    "pathway_map",
    "Draw the BIOLOGICAL-CONTEXT map for ONE gene: where it sits in its signalling neighbourhood — "
    "the gene as a node among its REAL pathway partners (from the code-owned enrichment), coloured by "
    "verdict, with candidate mechanisms shown as clearly-marked hypotheses for a disagreement. Call "
    "this for 'where does GENE sit', 'show the biology / pathway context of GENE', 'what pathway is "
    "GENE in', 'why might the layers disagree', or when the user wants a richer BIOLOGICAL figure than "
    "the bench sketch. Partners and pathway are CODE-derived (never invented); hypotheses are marked "
    "as hypotheses, not facts. Renders as a figure — give ONE plain lead-in sentence, do not restate "
    "the partners or quote numbers.",
    {
        "type": "object",
        "properties": {
            "gene": {"type": "string", "description": "Gene symbol, e.g. ZAP70."},
            "condition": {
                "type": "string",
                "description": "Optional activation condition: Rest, Stim8hr, or Stim48hr.",
            },
        },
        "required": ["gene"],
    },
)
async def pathway_map(args):
    gene = (args.get("gene") or "").strip().upper()
    hits = _BY_GENE.get(gene)
    if not hits:
        return _text({"gene": gene, "error": "not in the screens",
                      "note": "Only genes present in both CRISPR screens can be mapped."})
    by_cond = {r["condition"]: r for r in hits}
    requested = _resolve_condition(args.get("condition"), list(by_cond.keys()))
    anchor = requested or next((c for c in _SKETCH_ANCHOR_ORDER if c in by_cond),
                               hits[0]["condition"])
    return _pathway_map_view(gene, by_cond, anchor)


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
         "note": "One plain lead-in sentence only. Reflect both the code-computed advancement "
                 "stance and the unresolved evidence in plain words (advance / validate first / "
                 "hold as a weak target / deprioritise) — e.g. '" + gene
                 + " is discordant and weakly actionable; validate the split before committing "
                 "to it as a target.'. Never call the biology real or validated before the matched "
                 "experiment reproduces it. The card carries the detail — do NOT restate the experiment, "
                 "explanations, dossier scores, or numbers."},
        {"action": "plan", "gene": gene, "decision_brief": brief},
    )


def build_concord_server():
    """The in-process MCP server hosting Concord's tools."""
    # Imported here, not at module top: protein_tools imports _GENES/_view FROM this module, so a
    # top-level import would be circular. By the time the server is built this module is fully loaded.
    from .protein_tools import protein_report
    return create_sdk_mcp_server(
        name="concord",
        version="0.1.0",
        tools=[reconcile_gene, compare_conditions, gene_evidence, known_biology,
               draft_decision_brief, hitlist_biology, sketch_gene, rank_targets, pathway_map,
               protein_report],
    )


CONCORD_ALLOWED_TOOLS = [
    "mcp__concord__reconcile_gene",
    "mcp__concord__compare_conditions",
    "mcp__concord__gene_evidence",
    "mcp__concord__known_biology",
    "mcp__concord__draft_decision_brief",
    "mcp__concord__hitlist_biology",
    "mcp__concord__sketch_gene",
    "mcp__concord__rank_targets",
    "mcp__concord__pathway_map",
    "mcp__concord__protein_report",
]
