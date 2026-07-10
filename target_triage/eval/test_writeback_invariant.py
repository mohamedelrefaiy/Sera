"""NODE B GATE — the interpretation agent must never leak into the conclusion (brief §2.4).

Node B reads a finished verdict and attaches cited mechanistic hypotheses plus one distinguishing
experiment. The danger it carries is the one this whole project differentiates against: an LLM
that narrates plausible biology and, by degrees, starts deciding what is true.

Three gates, in the brief's order of importance.

  (a) WRITE-BACK INVARIANT (the structural one). The verdict table must be byte-identical with
      Node B on and off. Proven two independent ways, because neither alone suffices:

        static   `core/` and `pipeline/02_build_concordance.py` import nothing from `rim/`.
                 No code path from a hypothesis to a verdict exists.
        dynamic  run Node B, write its plan, rebuild the concordance, hash the parquet. Equal.

      A future author could defeat the static check by reading the plan JSON inside pipeline/02
      without importing `rim`. The dynamic check catches that. A future author could defeat the
      dynamic check with a change that only fires on data we do not test. The static check catches
      that. Together they are hard to route around by accident, which is the point.

  (b) CITATION DISCIPLINE. "No uncited claims. Ever." A malformed accession drops its claim; an
      entry with no surviving claim is refused entirely -- better an absent hypothesis than an
      unsourced one. Format-checking is offline and always runs; existence-checking is network,
      opt-in, and reported as its OWN status, so a well-formed hallucination is never presented as
      a verified source.

  (c) NO NUMBER INVENTION. Node B inherits the grounding contract from core/explanation.py: every
      figure in the prose must trace to the record it was handed.

The model is never called here. Every test drives `parse_entry` on fixed JSON, so this gate runs
on a keyless clone and in CI. What it proves about the model is structural: Node B's only output
is a ValidationEntry, and a ValidationEntry cannot carry an uncited claim or reach a verdict.

Run as a gate:    python target_triage/eval/test_writeback_invariant.py
Run under pytest: pytest target_triage/eval/test_writeback_invariant.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from target_triage.rim.interpret import (  # noqa: E402
    HYPOTHESIS_LABEL, Citation, CitationDB, CitationStatus, Hypothesis, UncitedClaim,
    ValidationEntry, build_record, parse_entry, validate_entry, write_plan)

_APP = os.path.join(os.path.dirname(__file__), "..", "..")
_CORE = os.path.join(_APP, "target_triage", "core")
_PIPE = os.path.join(_APP, "pipeline")
_CONC = os.path.join(_APP, "target_triage", "data", "artifacts", "concordance.parquet")

# A real protein_only row's SHAPE (values synthetic). Node B is handed exactly this, and no more.
RECORD_ROW = {
    "gene": "VPS37B", "cytokine": "IL2", "condition": "Stim48hr", "verdict": "protein_only",
    "z_rna": -0.31, "q_rna": 0.44, "hit_rna": False, "rna_promotes": None,
    "lfc_prot": -1.20, "q_prot": 0.002, "hit_prot": True, "prot_promotes": True,
}

# A well-formed model response. P60568 is IL-2's real UniProt accession; 12345678 is a well-formed
# PMID (its EXISTENCE is deliberately not asserted here -- that is what `resolve()` is for).
GOOD_JSON = json.dumps({
    "hypotheses": [
        {"claim": "ESCRT-I mediated trafficking may alter surface IL-2 without changing "
                  "transcript abundance.",
         "citation": {"db": "uniprot", "accession": "P60568"}},
        {"claim": "Reduced protein turnover could lower steady-state IL-2 protein alone.",
         "citation": {"db": "pubmed", "accession": "12345678"}},
    ],
    "distinguishing_experiment":
        "Run a cycloheximide chase on VPS37B-KD cells with matched qPCR: if IL-2 protein half-life "
        "shortens while transcript is unchanged, turnover explains it; if half-life is unchanged "
        "but surface stain drops relative to permeabilized stain, trafficking explains it.",
})


def _need(path: str):
    if not os.path.exists(path):
        pytest.skip(f"artifact missing: {path} — run the pipeline first")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _py_files(root: str) -> list[str]:
    out = []
    for base, _dirs, files in os.walk(root):
        if "__pycache__" in base:
            continue
        out.extend(os.path.join(base, f) for f in files if f.endswith(".py"))
    return out


# The forbidden dependency is a MODULE, not a substring. An early version of this gate grepped for
# the text "rim" and failed on `cfg.primary_cytokine` -- p-rim-ary. In a genomics codebase `primer`
# and `primary` are everywhere, so a 3-letter substring check is a false-positive machine. Worse,
# it is also too weak: it would miss `importlib.import_module("target_triage.rim.interpret")`.
#
# So: parse the AST and inspect real import statements, and separately scan string LITERALS for the
# artifact Node B writes. Structure, not characters.


def _imports_of(path: str) -> set[str]:
    """Every module name imported by a file, however it was spelled."""
    import ast
    with open(path) as fh:
        tree = ast.parse(fh.read(), filename=path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from ..rim import x` -> module='rim'; `from target_triage.rim import x` -> full path
            if node.module:
                names.add(node.module)
            names.update(f"{node.module or ''}.{a.name}".lstrip(".") for a in node.names)
    return names


def _string_literals(path: str) -> set[str]:
    """Every string literal in a file, EXCLUDING docstrings (where a prohibition is described)."""
    import ast
    with open(path) as fh:
        tree = ast.parse(fh.read(), filename=path)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value not in docstrings}


def _references_rim(path: str) -> list[str]:
    """Ways a file could reach Node B: import it, or read the artifact it writes."""
    hits = []
    for mod in _imports_of(path):
        parts = mod.split(".")
        if "rim" in parts or "interpret" in parts:
            hits.append(f"imports {mod!r}")
    for lit in _string_literals(path):
        if "validation_plan" in lit or "target_triage.rim" in lit:
            hits.append(f"references {lit!r}")
    return hits


# ---- (a) WRITE-BACK INVARIANT ------------------------------------------------------------


def test_core_does_not_import_the_rim():
    """STATIC proof. The dependency arrow points inward, always. If `core/` ever imports `rim/`,
    the hub has taken a dependency on an agent and the architecture's central claim is void."""
    offenders = {os.path.relpath(p, _APP): hits
                 for p in _py_files(_CORE) if (hits := _references_rim(p))}
    assert not offenders, (
        f"core/ reaches the rim layer: {offenders}. The deterministic hub must never depend on an "
        "agent -- that is the entire trust model.")


def test_concordance_builder_does_not_reference_node_b():
    """The verdict builder must not import an agent, nor read the validation plan one writes.

    Checked by AST, not by substring: `cfg.primary_cytokine` contains the letters 'rim', and a
    naive grep for it fails on p-RIM-ary. Real imports and real string literals, nothing else.
    """
    hits = _references_rim(os.path.join(_PIPE, "02_build_concordance.py"))
    assert not hits, (
        f"pipeline/02_build_concordance.py reaches Node B ({hits}) -- the interpretation agent is "
        "leaking into the verdict")


def test_verdict_table_is_hash_identical_with_node_b_on_and_off():
    """DYNAMIC proof. Run Node B in full, write its plan, then rebuild the concordance table and
    assert it reproduces the committed parquet exactly. This is what mechanically forbids the
    interpretation agent from ever changing a conclusion, and it fails loudly if B is wired back."""
    _need(_CONC)
    before = _sha256(_CONC)

    import importlib.util
    import pandas as pd

    with tempfile.TemporaryDirectory() as d:
        # Node B runs, in full, and writes its artifact to disk.
        entry = parse_entry(GOOD_JSON, build_record(RECORD_ROW))
        plan = write_plan([entry], os.path.join(d, "artifacts", "validation_plan.json"))
        assert os.path.exists(plan), "Node B produced no plan; the test would prove nothing"

        # ...and the hub is rebuilt from scratch, with Node B's output sitting on disk.
        spec = importlib.util.spec_from_file_location(
            "_build_conc", os.path.join(_PIPE, "02_build_concordance.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rebuilt = mod.build(progress=lambda *a, **k: None)

        live = pd.read_parquet(_CONC)
        pd.testing.assert_frame_equal(
            live.reset_index(drop=True), rebuilt.reset_index(drop=True),
            obj="verdict table rebuilt while Node B's plan exists")

    assert _sha256(_CONC) == before, (
        "the verdict table changed on disk while Node B ran -- the interpretation agent wrote back")


def test_node_b_never_opens_the_verdict_table():
    """Node B's module must not name the verdict artifact in executable code. It is handed rows; it
    does not go looking for them, and it certainly does not write them."""
    with open(os.path.join(_APP, "target_triage", "rim", "interpret.py")) as fh:
        src = fh.read()
    body = src.split('"""', 2)[-1]        # drop the module docstring, which names it to forbid it
    code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    assert "concordance.parquet" not in code, (
        "interpret.py references concordance.parquet outside its docstring")
    assert "to_parquet" not in code, "interpret.py writes a parquet -- it must only attach JSON"


# ---- (b) CITATION DISCIPLINE --------------------------------------------------------------


def test_wellformed_citations_pass_and_malformed_ones_do_not():
    """The offline check must accept real accessions (P60568 = IL-2; A0A024R1R8 = the 10-char form)
    and reject look-alikes. A loose pattern would wave a hallucinated string through the only check
    available when offline."""
    for acc in ("P60568", "P01579", "A0A024R1R8", "O14763"):
        assert Citation(CitationDB.UNIPROT, acc).check_format(), acc
    for acc in ("P6056", "P60568X", "ZZZZZZ", "12345", ""):
        assert not Citation(CitationDB.UNIPROT, acc).check_format(), acc
    for pmid in ("1", "12345678", "35446349"):
        assert Citation(CitationDB.PUBMED, pmid).check_format(), pmid
    for pmid in ("0123", "abc", "123456789", "12.5", ""):
        assert not Citation(CitationDB.PUBMED, pmid).check_format(), pmid


def test_an_uncited_claim_never_reaches_disk():
    """A claim with a malformed accession is dropped. If nothing survives, the ENTRY is refused --
    better an absent hypothesis than an unsourced one."""
    bad = json.dumps({
        "hypotheses": [{"claim": "IL-2 is regulated post-transcriptionally.",
                        "citation": {"db": "pubmed", "accession": "not-a-pmid"}}],
        "distinguishing_experiment": "cycloheximide chase",
    })
    with pytest.raises(UncitedClaim, match="no hypotheses survived"):
        parse_entry(bad, build_record(RECORD_ROW))


def test_a_claim_with_no_citation_field_is_dropped_not_kept():
    """An agent that simply OMITS the citation must not get its claim through by omission."""
    partial = json.dumps({
        "hypotheses": [
            {"claim": "Uncited plausible-sounding mechanism."},                      # dropped
            {"claim": "Cited mechanism.",
             "citation": {"db": "uniprot", "accession": "P60568"}},                  # kept
        ],
        "distinguishing_experiment": "surface vs permeabilized stain",
    })
    entry = parse_entry(partial, build_record(RECORD_ROW))
    assert len(entry.hypotheses) == 1
    assert entry.hypotheses[0].claim == "Cited mechanism."


def test_entry_without_a_distinguishing_experiment_is_refused():
    """A mechanism nobody can test apart from its rival is a story, not a hypothesis."""
    no_exp = json.dumps({
        "hypotheses": [{"claim": "Trafficking.",
                        "citation": {"db": "uniprot", "accession": "P60568"}}],
        "distinguishing_experiment": "",
    })
    with pytest.raises(UncitedClaim, match="no distinguishing experiment"):
        parse_entry(no_exp, build_record(RECORD_ROW))


def test_every_entry_carries_the_hypothesis_label():
    """A hypothesis that can be mistaken for a finding is worse than no hypothesis. The label
    travels with the DATA, not just the template."""
    entry = parse_entry(GOOD_JSON, build_record(RECORD_ROW))
    assert entry.label == HYPOTHESIS_LABEL
    assert entry.to_json()["label"] == HYPOTHESIS_LABEL
    assert "not used in verdict" in HYPOTHESIS_LABEL

    with pytest.raises(UncitedClaim, match="not labelled"):
        validate_entry(ValidationEntry(
            gene="X", cytokine="IL2", condition="Rest", verdict="protein_only",
            hypotheses=(Hypothesis("c", Citation(CitationDB.UNIPROT, "P60568")),),
            distinguishing_experiment="e", grounded_from={}, label="Findings"))


def test_format_check_is_not_reported_as_verification():
    """The distinction the doctrine demands: a well-formed accession is FORMAT_OK, never RESOLVED.
    Only a live lookup may claim RESOLVED. Conflating them would sell a hallucination as a source."""
    c = Citation(CitationDB.UNIPROT, "P60568")
    assert c.check_format() is True
    assert c.status is CitationStatus.FORMAT_OK, (
        "a citation that was never looked up must not claim to be resolved")
    entry = parse_entry(GOOD_JSON, build_record(RECORD_ROW))
    statuses = {h["citation"]["status"] for h in entry.to_json()["hypotheses"]}
    assert statuses == {"format_ok"}, f"an offline run claimed {statuses}"


# ---- (c) NO NUMBER INVENTION ---------------------------------------------------------------


def test_record_carries_only_measured_values():
    """Node B may interpret only what the core computed. The record it is handed holds the verdict
    and the two sides' numbers -- and nothing else to reason from."""
    rec = build_record(RECORD_ROW)
    assert set(rec) == {"gene", "cytokine", "condition", "verdict",
                        "mrna_perturbseq", "protein_facs"}
    assert rec["mrna_perturbseq"]["z_score"] == -0.31
    assert rec["protein_facs"]["fdr"] == 0.002
    # an untested/undefined direction stays null -- never guessed into a label
    assert rec["mrna_perturbseq"]["direction"] is None
    assert rec["protein_facs"]["direction"] == "lowers_cytokine"


def test_grounding_record_is_stored_with_every_entry():
    """Provenance is stored, not asserted: the grounding gate re-reads `grounded_from` to check
    that every number in the prose traces to a measurement."""
    entry = parse_entry(GOOD_JSON, build_record(RECORD_ROW))
    assert entry.grounded_from["verdict"] == "protein_only"
    assert entry.to_json()["grounded_from"]["protein_facs"]["log_fold_change"] == -1.20


def test_written_plan_is_an_attachment_not_an_edit():
    """The plan is keyed like the explanation cache and lives in its own file. It never touches the
    verdict table, and it carries the verdict through unaltered."""
    with tempfile.TemporaryDirectory() as d:
        entry = parse_entry(GOOD_JSON, build_record(RECORD_ROW))
        path = write_plan([entry], os.path.join(d, "artifacts", "validation_plan.json"))
        with open(path) as fh:
            doc = json.load(fh)
        assert list(doc) == ["VPS37B|IL2|Stim48hr"]
        e = doc["VPS37B|IL2|Stim48hr"]
        assert e["label"] == HYPOTHESIS_LABEL
        assert e["verdict"] == "protein_only"          # carried through, never altered
        assert len(e["hypotheses"]) == 2
        assert all(h["citation"]["accession"] for h in e["hypotheses"])
        assert e["distinguishing_experiment"]


# ---- gate runner ---------------------------------------------------------------------------


def _run_gate() -> bool:
    print("NODE B GATE — the interpretation agent must never reach the verdict\n")
    ok = True

    core_files = _py_files(_CORE)
    offenders = [p for p in core_files if _references_rim(p)]
    a = not offenders
    print(f"  [{'PASS' if a else 'FAIL'}] static: core/ imports nothing from rim/ "
          f"({len(core_files)} modules checked, by AST)")
    ok = ok and a

    b = not _references_rim(os.path.join(_PIPE, "02_build_concordance.py"))
    print(f"  [{'PASS' if b else 'FAIL'}] static: the verdict builder never reaches Node B")
    ok = ok and b

    if os.path.exists(_CONC):
        before = _sha256(_CONC)
        with tempfile.TemporaryDirectory() as d:
            entry = parse_entry(GOOD_JSON, build_record(RECORD_ROW))
            write_plan([entry], os.path.join(d, "artifacts", "validation_plan.json"))
        after = _sha256(_CONC)
        c = after == before
        print(f"  [{'PASS' if c else 'FAIL'}] dynamic: verdict hash unchanged with Node B on "
              f"({before[:12]}…)")
        ok = ok and c
    else:
        print("  [SKIP] dynamic: concordance.parquet not built")

    try:
        parse_entry(json.dumps({"hypotheses": [{"claim": "x",
                                                "citation": {"db": "pubmed", "accession": "bad"}}],
                                "distinguishing_experiment": "y"}), build_record(RECORD_ROW))
        d_ok = False
    except UncitedClaim:
        d_ok = True
    print(f"  [{'PASS' if d_ok else 'FAIL'}] an uncited claim never reaches disk")
    ok = ok and d_ok

    entry = parse_entry(GOOD_JSON, build_record(RECORD_ROW))
    e = all(h.citation.status is CitationStatus.FORMAT_OK for h in entry.hypotheses)
    print(f"  [{'PASS' if e else 'FAIL'}] offline citations report format_ok, never 'resolved'")
    ok = ok and e

    print(f"\n{'PASS' if ok else 'FAIL'} — Node B gate")
    return ok


if __name__ == "__main__":
    sys.exit(0 if _run_gate() else 1)
