"""Grounded-explanation prompt + record shape — the ONE source of truth.

The rule (brief §7.3): Claude interprets ONLY the measured numbers in the record; it never
invents a p-value, effect size, or figure, and calls an absent side "untested". That contract
is enforced by a grounding test (eval/test_explanations.py), so the prompt and the record shape
must not drift. Both the offline precompute (pipeline/04_explanations.py) and the live streaming
endpoint (api/app.py: /api/explanation/...) import from here — change the science in one place.

This module is stdlib-only: it builds strings/dicts, it does NOT call the model. The caller owns
the transport (a one-shot cached call offline, an SSE stream live).
"""
from __future__ import annotations

from typing import Any

MODEL = "claude-haiku-4-5-20251001"   # cheap, grounded, high-volume interpretation

# One-line role annotations for the demo genes. Claude may use these for MECHANISM only, never
# to state a number. A gene without one gets an empty annotation (no fabricated biology).
ANNOTATIONS: dict[str, str] = {
    "ITK": "IL2-inducible T-cell kinase; TCR signaling.",
    "BCL10": "CARD11-BCL10-MALT1 signalosome; NF-kB activation.",
    "VAV1": "guanine-nucleotide exchange factor; proximal TCR signaling.",
    "TSC1": "TSC complex; restrains mTOR (a brake on activation).",
    "LCP2": "SLP-76 adaptor; relays the TCR signal.",
    "VPS37B": "ESCRT-I component; membrane trafficking.",
    "ZNF250": "zinc-finger protein; uncharacterized in this context.",
    "IL2RA": "IL-2 receptor alpha (CD25); surface receptor.",
}

SYSTEM_PROMPT = (
    "You explain CRISPR-screen concordance to a bench immunologist. You are given a JSON record "
    "of MEASURED values plus a one-line gene annotation.\n"
    "Rules:\n"
    "- Use ONLY the numbers in the record. NEVER invent a p-value, q-value, effect size, or any "
    "figure not present. If a value is absent or null, say that side is untested — do not guess.\n"
    "- 2-3 sentences, plain language, no hedging boilerplate.\n"
    "- For a discordant verdict, note the two screens disagree on DIRECTION and, using the "
    "annotation for mechanism only, suggest why (e.g. a brake that lowers transcript but raises "
    "protein). For protein_only, suggest a post-transcriptional mechanism.\n"
    "- Do NOT claim novelty; this is a reconciliation, not a discovery.\n"
    "- Refer to the mRNA side as the Perturb-seq screen and the protein side as the FACS screen."
)


def _direction_label(promotes: bool | None) -> str | None:
    """None (undefined/untested) -> null; True (KD lowers cytokine) -> lowers; False -> raises."""
    if promotes is None:
        return None
    return "lowers_cytokine" if promotes else "raises_cytokine"


def build_record(row: dict[str, Any], gene: str) -> dict[str, Any]:
    """The exact numeric record Claude is allowed to interpret — nothing more.

    `row` must already be JSON-clean (numpy/NaN normalised to plain floats/None). Keys read:
    cytokine, condition, verdict, z_rna, q_rna, hit_rna, rna_promotes, lfc_prot, q_prot,
    hit_prot, prot_promotes.
    """
    return {
        "gene": gene,
        "cytokine": row["cytokine"],
        "condition": row["condition"],
        "verdict": row["verdict"],
        "mrna_perturbseq": {
            "z_score": row["z_rna"], "adj_p_value": row["q_rna"], "is_hit": row["hit_rna"],
            "direction": _direction_label(row["rna_promotes"]),
        },
        "protein_facs": {
            "log_fold_change": row["lfc_prot"], "fdr": row["q_prot"], "is_hit": row["hit_prot"],
            "direction": _direction_label(row["prot_promotes"]),
        },
        "gene_annotation": ANNOTATIONS.get(gene, ""),
    }


def user_prompt(record: dict[str, Any]) -> str:
    """The single-turn user message: the instruction + the JSON record, numbers-only."""
    import json
    return ("Explain this concordance record in 2-3 sentences for a bench immunologist. "
            "Use ONLY the numbers given:\n" + json.dumps(record, indent=2))
