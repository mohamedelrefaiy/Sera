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
    "You explain a CRISPR-screen reconciliation to an EXPERIMENTAL bench immunologist who runs "
    "screens but does not read statistics off the top of their head. The record gives you a `plain` "
    "summary of what the two screens found, plus a one-line gene annotation.\n"
    "How to write:\n"
    "- Speak their language: what the knockout DID, what each screen SAW, and why it matters for "
    "the next decision. Follow the arc: perturbation, transcript result, protein result, practical "
    "consequence, unresolved question.\n"
    "- NEVER quote a statistic in your sentences. Do not write a z-score, log-fold-change, p-value, "
    "FDR, or q-value. Say 'went down', 'went up', 'a strong, confident effect', 'a weaker signal', "
    "'no measurable change' — words, not figures. The numbers sit in the figure beside your text; "
    "your job is the meaning, not the readout.\n"
    "- Ground every claim in the `plain` summary. NEVER invent a direction, a strength, or a "
    "mechanism the record does not support. The gene annotation is context, not evidence for why "
    "the two screens differ. If a side is untested, say that screen did not measure this gene; do "
    "not guess.\n"
    "- Name the assays in words: 'the Perturb-seq screen (which reads transcript)' and 'the FACS "
    "screen (which reads protein)'.\n"
    "- For a `discordant` result, say plainly the two screens point in OPPOSITE directions. State "
    "that this is not a simple failed repeat because the screens measured different biological "
    "outputs in separate experiments. Explain that an mRNA-only ranking would predict the protein "
    "response incorrectly. Present post-transcriptional control, secretion, screen context, and "
    "assay error only as possibilities; NEVER call any of them the explanation, a signature, or "
    "established biology. End with the paired measurement needed before advancing or discarding "
    "the target. For `protein_only`, say the protein moved while the transcript did not, so the gene "
    "was detected only by the protein screen here — a post-transcriptional effect is one hypothesis "
    "for that gap (not established), and a transcript-only screen would miss the gene either way.\n"
    "- Never say the pattern 'makes sense' unless the supplied record contains direct evidence for "
    "the mechanism. Do not infer translation, protein stability, feedback, or secretion from a gene "
    "annotation alone.\n"
    "- 2-4 sentences. No hedging boilerplate. Do NOT claim novelty; this is a reconciliation, not a "
    "discovery. Do NOT open with the verdict word as a header — write in flowing prose."
)


def _direction_label(promotes: bool | None) -> str | None:
    """None (undefined/untested) -> null; True (KD lowers cytokine) -> lowers; False -> raises.

    Kept for the machine-readable record and for rim/interpret.py which imports it. The
    experimentalist PROSE narrates from the `plain` block (down/up words) instead — see _plain_side.
    """
    if promotes is None:
        return None
    return "lowers_cytokine" if promotes else "raises_cytokine"


# The `plain` block turns a hit-flag + FDR/q into words an experimentalist reads without a stats
# table, so the PROSE never needs to touch a stat key. The raw numbers still ride along in the
# record (mrna_perturbseq / protein_facs) so the grounding test can prove nothing was invented and
# the figure reads from the same source — the prompt forbids QUOTING them.
_STRONG_Q = 0.01   # q at/below this reads as a strong, confident effect; above it, a clear change.


def _plain_side(promotes: bool | None, is_hit: bool | None, q: float | None) -> dict[str, Any]:
    """Words-only summary of one screen's result: tested?, which way (down/up), how strong."""
    if is_hit is None:
        return {"tested": False, "change": None, "strength": "not measured"}
    if not is_hit:
        return {"tested": True, "change": None, "strength": "no measurable change"}
    change = "down" if promotes else "up" if promotes is not None else None
    strength = "a strong, confident change" if (q is not None and q <= _STRONG_Q) else "a clear change"
    return {"tested": True, "change": change, "strength": strength}


def build_record(row: dict[str, Any], gene: str) -> dict[str, Any]:
    """The record Claude interprets. `plain` (words) is what the PROSE narrates from; the
    mrna_perturbseq / protein_facs blocks keep the raw numbers so the grounding test can verify
    nothing was invented and the figure reads the same source. The prompt forbids quoting numbers.

    `row` must already be JSON-clean (numpy/NaN normalised to plain floats/None). Keys read:
    cytokine, condition, verdict, z_rna, q_rna, hit_rna, rna_promotes, lfc_prot, q_prot,
    hit_prot, prot_promotes.
    """
    return {
        "gene": gene,
        "cytokine": row["cytokine"],
        "condition": row["condition"],
        "verdict": row["verdict"],
        "plain": {
            "transcript_perturbseq": _plain_side(row["rna_promotes"], row["hit_rna"], row["q_rna"]),
            "protein_facs": _plain_side(row["prot_promotes"], row["hit_prot"], row["q_prot"]),
        },
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
    """The single-turn user message: narrate from `plain`, in the experimentalist voice."""
    import json
    return ("Explain this reconciliation in 2-4 plain sentences for a bench immunologist. Narrate "
            "from the `plain` summary — what the knockout did to the transcript and the protein, and "
            "why. NEVER quote any number (z-score, log-fold-change, p, FDR); those are for the "
            "figure, not your text:\n" + json.dumps(record, indent=2))
