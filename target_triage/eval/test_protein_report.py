"""PROTEIN MINI-REPORT CONTROL — the identity/structure/literature card, made honesty-proof.

The mini-report answers "what do we know about this protein": its UniProt identity, its best 3D
structure, and a short CITED literature summary. Every one of those is an external FACT, and the
whole reason this feature is safe is that CODE retrieves each one and the LLM only selects from a
retrieved list -- it never authors an accession, a PDB id, or a PMID. This gate pins that.

It is a POSITIVE CONTROL. The card is not "done" until this prints PASS. Two things are proven:

  IDENTITY  Known proteins resolve to their CORRECT, real accession -- the exact failure the
            interpretation node had (it once cited Q16558 for TSC1, a potassium channel; TSC1 is
            Q92574). We assert the RIGHT accession, an on-pattern shape, and a real structure id.
            This tier needs the network (UniProt/RCSB); it SKIPS offline rather than failing, so a
            plane-mode run of the suite is green, but a connected run proves the retrieval.

  DISCIPLINE The cite-by-index parse is unforgeable, OFFLINE and deterministic: a valid index is
            kept, an out-of-range index is dropped, a duplicate paper is dropped, and a model that
            writes a free-text `pmid`/`accession` is a HARD FAILURE (a regression that let the model
            author an identifier would be exactly the hallucinated-citation bug this design removes).

Run as a gate:      python eval/test_protein_report.py     (prints PASS/FAIL + numbers)
Run under pytest:   pytest eval/test_protein_report.py
"""
from __future__ import annotations

import os
import sys
import urllib.request
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from target_triage.clients.protein import ProteinRecord, resolve_protein  # noqa: E402
from target_triage.llm.literature import parse_panel  # noqa: E402
from target_triage.rim.interpret import Paper, UncitedClaim  # noqa: E402

# The identity truth set: gene -> its CANONICAL reviewed human UniProt accession. These are the
# right answers; the point of the gate is that the retrieval returns THESE, not a well-formed wrong
# one. TSC1 is here specifically because it is the accession the LLM got wrong from memory.
IDENTITY_CONTROLS: tuple[tuple[str, str], ...] = (
    ("TSC1", "Q92574"),     # Hamartin — the one the model hallucinated as Q16558
    ("IL2RA", "P01589"),    # IL-2 receptor alpha — has an approved antibody + solved structures
    ("NFKB2", "Q00653"),    # NF-kB p100 — the gene reviewed in the prior session
)


def _online(timeout: float = 6.0) -> bool:
    """Is the UniProt SEARCH endpoint reachable? Probe a real query, not the root — the root URL
    404/403s (it serves no resource), which would wrongly read as 'offline' and skip the tier. The
    identity tier needs the live API; if it is genuinely unreachable it SKIPS, never FAILS."""
    probe = ("https://rest.uniprot.org/uniprotkb/search?query=accession:P01589"
             "&format=json&size=1&fields=accession")
    try:
        with urllib.request.urlopen(probe, timeout=timeout) as r:  # noqa: S310 — fixed host
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


@dataclass(frozen=True)
class IdentityResult:
    gene: str
    expected_acc: str
    got_acc: str | None
    has_structure: bool
    structure_id: str | None
    passed: bool
    reason: str


def evaluate_identity(gene: str, expected_acc: str) -> IdentityResult:
    rec: ProteinRecord = resolve_protein(gene, use_cache=False)
    if not rec.resolved or not rec.accession:
        return IdentityResult(gene, expected_acc, None, False, None, False,
                              "UniProt returned no reviewed human entry")
    if rec.accession != expected_acc:
        return IdentityResult(gene, expected_acc, rec.accession, bool(rec.structure),
                              rec.structure.identifier if rec.structure else None, False,
                              f"got {rec.accession}, expected {expected_acc} (WRONG identity)")
    has_struct = rec.structure is not None
    sid = rec.structure.identifier if rec.structure else None
    reason = (f"acc={rec.accession} len={rec.length} "
              f"struct={rec.structure.source + ':' + sid if has_struct else 'none'}")
    return IdentityResult(gene, expected_acc, rec.accession, has_struct, sid, True, reason)


def run_identity() -> tuple[IdentityResult, ...]:
    return tuple(evaluate_identity(g, acc) for g, acc in IDENTITY_CONTROLS)


# --- the offline discipline set: synthetic model output against a fixed retrieved list ----------

_PAPERS = (Paper("11111111", "IL2RA is the alpha subunit of the high-affinity IL-2 receptor"),
           Paper("22222222", "CD25 marks regulatory T cells"))


@pytest.mark.skipif(not _online(), reason="UniProt unreachable; identity tier needs the network")
@pytest.mark.parametrize("gene,expected_acc", IDENTITY_CONTROLS)
def test_identity_resolves_to_the_right_accession(gene: str, expected_acc: str) -> None:
    result = evaluate_identity(gene, expected_acc)
    assert result.passed, f"{gene}: {result.reason}"
    assert result.has_structure, f"{gene}: resolved but no structure found ({result.reason})"


def test_valid_indices_are_kept() -> None:
    panel = parse_panel(
        '{"findings":[{"statement":"IL2RA is the IL-2 receptor alpha chain.","paper_index":0},'
        '{"statement":"It marks Tregs.","paper_index":1}]}', "IL2RA", _PAPERS)
    assert len(panel.findings) == 2
    assert [f.citation.accession for f in panel.findings] == ["11111111", "22222222"]


def test_out_of_range_index_is_dropped() -> None:
    panel = parse_panel(
        '{"findings":[{"statement":"valid","paper_index":0},'
        '{"statement":"phantom","paper_index":9}]}', "IL2RA", _PAPERS)
    assert len(panel.findings) == 1


def test_duplicate_paper_is_dropped() -> None:
    panel = parse_panel(
        '{"findings":[{"statement":"a","paper_index":0},'
        '{"statement":"b","paper_index":0}]}', "IL2RA", _PAPERS)
    assert len(panel.findings) == 1


def test_free_text_accession_is_a_hard_failure() -> None:
    # The model wrote an identifier it was told it could not write. This MUST raise, not drop --
    # silently dropping it would let the hallucinated-citation regression pass unnoticed.
    for forbidden in ('"pmid":"12345678"', '"accession":"P01589"', '"citation":"x"'):
        with pytest.raises(UncitedClaim):
            parse_panel('{"findings":[{"statement":"x",' + forbidden + '}]}', "IL2RA", _PAPERS)


def test_a_citation_can_only_come_from_a_retrieved_paper() -> None:
    panel = parse_panel('{"findings":[{"statement":"x","paper_index":0}]}', "IL2RA", _PAPERS)
    # the citation's accession is EXACTLY the retrieved paper's pmid -- unforgeable by construction
    assert panel.findings[0].citation.accession == _PAPERS[0].pmid
    assert panel.findings[0].citation.url == "https://pubmed.ncbi.nlm.nih.gov/11111111/"


if __name__ == "__main__":
    print("PROTEIN MINI-REPORT CONTROL — identity must be RIGHT, citations unforgeable\n")

    # Discipline tier (offline, always runs).
    discipline_ok = True
    checks = [
        ("valid indices kept",
         lambda: len(parse_panel('{"findings":[{"statement":"a","paper_index":0},'
                                 '{"statement":"b","paper_index":1}]}', "X", _PAPERS).findings) == 2),
        ("out-of-range dropped",
         lambda: len(parse_panel('{"findings":[{"statement":"a","paper_index":0},'
                                 '{"statement":"b","paper_index":9}]}', "X", _PAPERS).findings) == 1),
        ("duplicate dropped",
         lambda: len(parse_panel('{"findings":[{"statement":"a","paper_index":0},'
                                 '{"statement":"b","paper_index":0}]}', "X", _PAPERS).findings) == 1),
    ]
    for name, fn in checks:
        try:
            ok = bool(fn())
        except Exception:  # noqa: BLE001
            ok = False
        discipline_ok = discipline_ok and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] discipline · {name}")
    # the hard-failure check: writing a free-text pmid MUST raise
    try:
        parse_panel('{"findings":[{"statement":"x","pmid":"12345678"}]}', "X", _PAPERS)
        hard_ok = False
    except UncitedClaim:
        hard_ok = True
    discipline_ok = discipline_ok and hard_ok
    print(f"  [{'PASS' if hard_ok else 'FAIL'}] discipline · free-text pmid is a HARD failure")

    # Identity tier (needs the network).
    print()
    if not _online():
        print("  [SKIP] identity tier — UniProt unreachable (run connected to prove retrieval)")
        identity_ok = True
        identity_results: tuple[IdentityResult, ...] = ()
    else:
        identity_results = run_identity()
        width = max(len(r.gene) for r in identity_results)
        for r in identity_results:
            print(f"  [{'PASS' if r.passed else 'FAIL'}] identity   · {r.gene:<{width}} {r.reason}")
        identity_ok = all(r.passed and r.has_structure for r in identity_results)

    n_id = sum(r.passed for r in identity_results)
    print(f"\n{'discipline OK' if discipline_ok else 'discipline FAILED'}"
          + (f" · identity {n_id}/{len(identity_results)}" if identity_results else " · identity SKIPPED"))
    passed = discipline_ok and identity_ok
    print("\nRESULT: " + ("PASS — protein mini-report is honesty-proof"
                          if passed else "FAIL — see the failing check above"))
    sys.exit(0 if passed else 1)
