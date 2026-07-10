"""Phase A · step 06 — run the peripheral agent layer and emit the provenance log.

This is the step that makes the N-screen claim real rather than promised. It ingests every bundled
screen THROUGH the canonical schema -- profiling each file, proposing a column mapping, gating that
proposal, correcting an inverted sign if the data demands it -- and writes the two artifacts a
reviewer needs in order to check the work:

    mapping_manifest.yaml   how each screen was read (Node A): which column became which axis,
                            which significance columns were folded and by what rule, which regime
                            was chosen and which discarded, whether the effect sign was inverted
                            and on what evidence, and every column left unmapped.

    provenance.json         the single audit view. Node A's mapping decisions AND Node B's cited
                            claims, in one file, so any verdict can be traced back to the bytes it
                            came from, and any mechanistic sentence to its accession.

The provenance log is not a side artifact -- it IS the differentiator. A general agent produces a
conclusion. Concord produces a conclusion plus a receipt.

WHAT THIS STEP DOES NOT DO: it does not build, edit, or even open the verdict table for writing.
The concordance is built by pipeline/02_build_concordance.py from the deterministic core, and
nothing here can change it. That separation is asserted by eval/test_writeback_invariant.py.

Node B is OPTIONAL and off by default. With no Anthropic credentials the run still completes, and
the manifest records `proposer: heuristic` -- honest about how it got there. Passing --interpret
without credentials is an ERROR rather than a silent skip, because a demo that quietly produced no
hypotheses would look identical to one that produced them.

Run:  python pipeline/06_ingest.py                         # Node A only; writes both artifacts
      python pipeline/06_ingest.py --propose-with-claude   # let Claude propose the mappings
      python pipeline/06_ingest.py --interpret --genes VPS37B TSC1   # + Node B on those genes
      python pipeline/06_ingest.py --interpret --resolve-citations   # network: verify accessions
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from target_triage.core.canonical import SIGN_CONVENTION, Modality, SchemaViolation  # noqa: E402
from target_triage.rim.adapters import FREIMER_CSV, SCHMIDT_CSV, zhu_rows  # noqa: E402
from target_triage.rim.ingest import (  # noqa: E402
    AxisExtract, NeedsConfirmation, has_credentials, ingest_csv, manifest, profile_csv,
    propose_heuristic, propose_with_claude, write_manifest)

_ART = os.path.join(_APP, "target_triage", "data", "artifacts")
_MRNA = os.path.join(_ART, "cytokine_mrna_effects.parquet")
_CONC = os.path.join(_ART, "concordance.parquet")
_MANIFEST = os.path.join(_ART, "mapping_manifest.yaml")
_PROVENANCE = os.path.join(_ART, "provenance.json")

# One entry per screen the layer knows how to ingest. Adding an Nth screen is adding a row here --
# not touching the core.
#
# Two kinds of knowledge, kept strictly apart, because only one of them is in the file:
#
#   `hints`      claims ABOUT THE COLUMNS -- which column holds the cytokine, how to split a fused
#                label. A model reads these off a column profile reliably; they exist here only so
#                the heuristic proposer (which has no model) can run.
#
#   `constants`  claims ABOUT THE EXPERIMENT -- "the cells were stimulated", "these are CD4 T
#                cells". NOTHING in a column profile contains these facts, so a model asked for
#                them can only confabulate. Measured on Schmidt: Claude proposed
#                condition_const="unstimulated" three times running (the sort was on STIMULATED
#                cells), varying the capitalization, which would silently have made the join key
#                nondeterministic. Both errors are schema-VALID, so no validator could catch them.
#
# Hence: the model never sees the constants and cannot supply them. A human states them here, where
# a reviewer can see the claim and correct it.
SCREENS = (
    {"screen_id": "schmidt2022", "path": SCHMIDT_CSV, "modality": Modality.PROTEIN,
     "row_filter": lambda r: r.get("phenotype") == "CD4+ IL2",
     "hints": {"cytokine_col": "phenotype", "cytokine_extract": AxisExtract.LAST_TOKEN,
               "cell_type_col": "phenotype", "cell_type_extract": AxisExtract.FIRST_TOKEN},
     "constants": {"condition_const": "Stimulated"}},
    {"screen_id": "freimer2022_il2", "path": FREIMER_CSV, "modality": Modality.PROTEIN,
     "row_filter": lambda r: r.get("screen") == "IL2",
     "hints": {"cytokine_col": "screen"},
     "constants": {"condition_const": "Stimulated", "cell_type_const": "CD4"}},
)


def ingest_all(*, use_claude: bool, confirm: bool = True, progress=print) -> list[dict]:
    """Profile -> propose -> gate -> correct, for every bundled screen. Returns their manifests.

    `confirm=True` (the default) means a low-confidence proposal STOPS the run and asks a human.
    That is not a failure mode -- it is the feature. Observed live: asked to map Schmidt, Claude
    returned confidence 0.72 because `neg|lfc` vs `pos|lfc` "assumes the biological direction of
    interest is depletion". It is right that this is an assumption (they are numerically identical
    in MAGeCK, which the model could not know from a profile), and it was right to escalate rather
    than proceed. Pass --yes to accept the proposal anyway, which is a human's decision to record.
    """
    manifests: list[dict] = []
    for screen in SCREENS:
        sid, path = screen["screen_id"], screen["path"]
        profiles = profile_csv(path)
        progress(f"[{sid}] profiled {len(profiles)} columns of {os.path.basename(path)}")

        if use_claude:
            # Claude maps the COLUMNS; the human-stated experiment constants are merged in, and any
            # constant Claude volunteers is discarded (see rim/ingest._parse_mapping).
            mapping = asyncio.run(propose_with_claude(profiles, sid, modality=screen["modality"],
                                                      axes=screen["constants"]))
            progress(f"[{sid}] Claude proposed a mapping (confidence {mapping.confidence:.2f})")
        else:
            mapping = propose_heuristic(profiles, sid, modality=screen["modality"],
                                        **screen["hints"], **screen["constants"])

        try:
            result = ingest_csv(path, mapping, row_filter=screen["row_filter"],
                                require_confirmation=confirm)
        except NeedsConfirmation as e:
            # The one place a human is required. Surfaced, never resolved by the model that made it.
            progress(f"[{sid}] NEEDS CONFIRMATION — {e.reason}")
            progress(f"[{sid}]   proposed: gene={e.mapping.gene_col!r} "
                     f"effect={e.mapping.effect_col!r} signif={e.mapping.signif_cols} "
                     f"regime={e.mapping.signif_regime.value}")
            progress(f"[{sid}]   re-run with --yes to accept this mapping (a human's decision, "
                     "recorded in the manifest)")
            raise

        flag = "  SIGN CORRECTED" if result.sign_corrected else ""
        progress(f"[{sid}] {result.report.n_rows:,} rows validated "
                 f"({result.report.regime.value}, {result.report.modality.value}){flag}")
        if result.sign_corrected:
            progress(f"[{sid}]   evidence: {result.source_report.sign_check.detail}")
        if result.report.unmapped_columns:
            progress(f"[{sid}]   {len(result.report.unmapped_columns)} columns unmapped "
                     "(recorded, not discarded)")
        manifests.append(manifest(result, path, run_label=""))
    return manifests


def mrna_note(progress=print) -> dict:
    """The mRNA screen's manifest entry. It is NOT re-ingested here -- it arrives as an already
    long-form parquet from step 01, which also consumed its on-target rows.

    So its Hazard 3 status is recorded honestly as `not_checkable`, with the reason. The
    alternative -- printing `inverted: false` -- would read as "checked, and fine" when the truth is
    "the calibration standard was spent upstream". Its polarity is instead established at source (a
    DESeq2 z-score, where negative = knockdown lowers the transcript) and corroborated by the sanity
    gate's positive controls: ITK/BCL10/VAV1 come out replicated, which they could not if this
    screen's sign were flipped.
    """
    if not os.path.exists(_MRNA):
        return {}
    import pandas as pd
    rows = zhu_rows(pd.read_parquet(_MRNA).query("cytokine == 'IL2'").to_dict("records"),
                    cytokine="IL2")
    progress(f"[zhu2025] {len(rows):,} mRNA rows (long-form from step 01; not re-ingested)")
    return {
        "screen_id": "zhu2025", "source_file": os.path.basename(_MRNA),
        "proposer": "pipeline/01_extract_cytokines.py", "confidence": 1.0,
        "mapping": {"gene": "target_contrast_gene_name", "effect_size": "zscore",
                    "significance": {"columns": ["adj_p_value"], "combine": "single",
                                     "regime": "deseq2_adjp"},
                    "modality": "rna"},
        "hazards": {
            "h1_ontarget_rows_found": sum(1 for r in rows if r.is_ontarget),
            "h2_regime_chosen": "deseq2_adjp",
            "h3_sign_check": "not_checkable",
            "h3_reason": ("on-target rows were consumed by Hazard 1 during extraction, so no "
                          "calibration standard remains. Polarity is established at source "
                          "(DESeq2 z-score: negative = knockdown lowers the transcript) and "
                          "corroborated by the sanity gate's positive controls."),
        },
        "unmapped_columns": [],
        "validation": {"n_rows": len(rows), "regime": "deseq2_adjp", "modality": "rna",
                       "sign_convention": SIGN_CONVENTION},
    }


def cited_claims(genes: list[str], *, resolve_citations: bool, progress=print) -> list[dict]:
    """Node B, opt-in. One provenance record per cited claim. Reads the verdict; never writes it."""
    import pandas as pd

    from target_triage.rim.interpret import build_record, interpret, resolve

    if not os.path.exists(_CONC):
        progress("[interpret] concordance.parquet absent — skipping Node B")
        return []

    df = pd.read_parquet(_CONC)
    out: list[dict] = []
    for gene in genes:
        rows = df[(df["gene"] == gene) & (df["condition"] == "Stim48hr")]
        if rows.empty:
            progress(f"[interpret] {gene}: not in the verdict table — skipped")
            continue
        raw = rows.iloc[0].to_dict()
        clean = {k: (None if (isinstance(v, float) and pd.isna(v))
                     else (v.item() if hasattr(v, "item") else v)) for k, v in raw.items()}
        record = build_record(clean)
        try:
            entry = asyncio.run(interpret(record))
        except Exception as e:  # noqa: BLE001 — one failed gene must not sink the provenance log
            progress(f"[interpret] {gene}: FAILED ({e}) — no hypothesis recorded")
            continue

        for h in entry.hypotheses:
            citation = resolve(h.citation) if resolve_citations else h.citation
            out.append({"gene": entry.gene, "cytokine": entry.cytokine,
                        "condition": entry.condition, "verdict": entry.verdict,
                        "label": entry.label, "claim": h.claim,
                        "citation": {"db": citation.db.value, "accession": citation.accession,
                                     "status": citation.status.value, "url": citation.url}})
        progress(f"[interpret] {gene}: {len(entry.hypotheses)} cited hypotheses ({entry.verdict})")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the peripheral agent layer; write provenance.")
    ap.add_argument("--propose-with-claude", action="store_true",
                    help="let Claude propose each column mapping (still gated by the validator)")
    ap.add_argument("--interpret", action="store_true", help="also run Node B (needs credentials)")
    ap.add_argument("--genes", nargs="*", default=["VPS37B", "TSC1"],
                    help="genes to interpret when --interpret is passed")
    ap.add_argument("--resolve-citations", action="store_true",
                    help="network: confirm each accession exists (else only format is checked)")
    ap.add_argument("--yes", action="store_true",
                    help="accept a low-confidence mapping (a human's decision; recorded)")
    args = ap.parse_args()

    # An explicit request that silently produced nothing would be indistinguishable from one that
    # worked. Fail loudly instead.
    for flag, needs in (("--propose-with-claude", args.propose_with_claude),
                        ("--interpret", args.interpret)):
        if needs and not has_credentials():
            print(f"[06] {flag} needs Anthropic credentials; none found.")
            return 1

    try:
        manifests = ingest_all(use_claude=args.propose_with_claude, confirm=not args.yes)
    except NeedsConfirmation:
        # Not a crash: the gate did its job and stopped for a human. Exit 2 so a script can tell
        # "needs a decision" apart from "something is broken".
        print("\n[06] ingestion paused for confirmation — nothing was written.")
        return 2
    except SchemaViolation as e:
        print(f"\n[06] ingestion REFUSED (the proposal did not survive the gate): {e}")
        return 1

    note = mrna_note()
    if note:
        manifests.append(note)

    write_manifest(manifests, _MANIFEST)
    print(f"\n[write] {_MANIFEST}  ({len(manifests)} screens)")

    claims = (cited_claims(args.genes, resolve_citations=args.resolve_citations)
              if args.interpret else [])

    os.makedirs(_ART, exist_ok=True)
    with open(_PROVENANCE, "w") as fh:
        json.dump({"sign_convention": SIGN_CONVENTION, "screens": manifests, "claims": claims},
                  fh, indent=1)
    print(f"[write] {_PROVENANCE}  ({len(manifests)} screens, {len(claims)} cited claims)")

    inverted = [m["screen_id"] for m in manifests
                if m.get("hazards", {}).get("h3_sign_corrected")]
    if inverted:
        print(f"\n[audit] sign-corrected screens: {', '.join(inverted)}")
    print("[audit] every mapping decision and every claim is traceable in provenance.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
