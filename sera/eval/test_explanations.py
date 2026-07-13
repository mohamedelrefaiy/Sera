"""GROUNDING GATE — cached Claude explanations must only interpret provided numbers.

The whole value of the "why this verdict" panel is that Claude never invents a figure. This
gate parses every numeric value out of each cached explanation and asserts it traces to that
explanation's own record (the z-score, p/adj-p, log-FC, or FDR it was given) — with a small
tolerance for rounding and unit re-expression (a q of 0.0658 may appear as 0.066 or 6.6e-2).

A number in the prose that is NOT in the record is a hallucinated figure — the exact failure
the grounding rule forbids — and fails this gate.

The gate SKIPS if explanations_cache.json isn't built (a keyless clone uses the template
fallback and generates no cache), so it never blocks CI on a missing artifact.

Run:  pytest eval/test_explanations.py
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

_CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "artifacts", "explanations_cache.json")


def _load():
    if not os.path.exists(_CACHE):
        pytest.skip("explanations_cache.json not built — run `python pipeline/04_explanations.py`")
    with open(_CACHE) as fh:
        return json.load(fh)


def _record_numbers(rec: dict) -> set[float]:
    """Every measured number in the record, plus common re-expressions (rounded, abs)."""
    vals: set[float] = set()
    for side in ("mrna_perturbseq", "protein_facs"):
        for v in rec.get(side, {}).values():
            if isinstance(v, (int, float)):
                vals.add(float(v))
    out: set[float] = set()
    for v in vals:
        out.add(v)
        out.add(abs(v))
        out.add(round(v, 2))
        out.add(round(abs(v), 2))
        out.add(round(v, 3))
        out.add(round(abs(v), 3))
    return out


# superscript / subscript digits -> ascii, so `10⁻⁵` and `log₂` normalise cleanly.
_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
_SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _normalise_sci(text: str) -> str:
    """Turn every scientific-notation variant into plain `NeM` so a single float() parses it:
    `4.8e-5`, `4.8 × 10⁻⁵`, `4.8x10^-5`, `4.8×10-5` all become `4.8e-5`. Also fold sub/superscript
    digits to ascii (`log₂FC`, `10⁻⁵`). Do this BEFORE label matching so the exponent stays
    attached to its mantissa."""
    t = text.replace("−", "-").translate(_SUP).translate(_SUB)
    # unicode/spelled scientific notation -> e-notation (handles "× 10 -5", "x10^-5", "×10-5")
    t = re.sub(r"\s*[×x]\s*10\s*\^?\s*(-?\d+)", r"e\1", t)
    t = re.sub(r"(\d)\s*[eE]\s*(-?\d)", r"\1e\2", t)   # tighten "e - 5" -> "e-5"
    return t


# A "statistic" is a number attached to one of these labels — that's when Claude is CLAIMING a
# measurement (as opposed to naming IL-2, CD25, or a 48-hour timepoint). We only ground these.
# The number group captures an optional e-notation exponent as ONE token.
_STAT_LABEL = re.compile(
    r"(?:z|p|adj[\s._-]*p|adjusted\s*p|q|fdr|log[\s._-]*(?:2\s*)?fc|"
    r"log[\s._-]*fold[\s-]*change|log2fc|effect|score)\s*(?:-?value)?\s*[=:≈~]?\s*"
    r"(-?\d+\.?\d*(?:e-?\d+)?)",
    re.IGNORECASE)


# Names that contain digits and would otherwise be mis-read as measurements (gene/protein
# symbols, receptor names, timepoints). Scrubbed to a placeholder BEFORE number extraction.
_NAME_TOKENS = re.compile(
    r"\b(?:SLP-?76|IL-?2|IL2RA|CD25|CD3[A-Z]?|ZAP-?70|mTORC?1|5['′]?\s*UTR|3['′]?\s*UTR|"
    r"\d+\s*-?\s*hour|\d+\s*h\b|\d+\s*hr)\b", re.IGNORECASE)


def _prose_numbers(text: str) -> list[float]:
    """Pull only numbers presented AS A STATISTIC (attached to a stat label). This is the precise
    notion of a claimed measurement — it ignores 'IL-2', 'SLP-76', '48-hour', 'CD25' and other
    names (which are scrubbed first so their digits never read as claimed figures)."""
    t = _NAME_TOKENS.sub(" NAME ", text)
    t = _normalise_sci(t)
    out: list[float] = []
    for m in _STAT_LABEL.finditer(t):
        try:
            out.append(float(m.group(1)))
        except ValueError:
            continue
    return out


def _matches_record(n: float, record_nums: set[float]) -> bool:
    """A prose number is grounded if it is (near) a record number, or a plausible rounding /
    scientific re-expression of one."""
    for r in record_nums:
        if abs(n - r) < max(1e-3, abs(r) * 0.05):
            return True
    return False


@pytest.mark.parametrize("case", list(_load().items()) if os.path.exists(_CACHE) else [],
                         ids=lambda c: c[0] if isinstance(c, tuple) else str(c))
def test_explanation_invents_no_numbers(case):
    """Every numeric figure in the explanation must trace to a value in its own record."""
    key, entry = case
    rec = entry["grounded_from"]
    record_nums = _record_numbers(rec)
    text = entry["explanation"]

    hallucinated = []
    for n in _prose_numbers(text):
        if abs(n) < 1e-12:
            continue                          # 0 is prose, not a claim
        if n == int(n) and abs(n) < 50:
            continue                          # a bare small integer is a name digit (LCP2, CD3),
            #                                   never a z-score / p-value / log-FC in this domain
        if not _matches_record(n, record_nums):
            hallucinated.append(n)
    assert not hallucinated, (
        f"{key}: explanation cites number(s) not in the record {sorted(hallucinated)} "
        f"(record has {sorted(record_nums)}). Text: {text!r}")


def test_all_cached_explanations_have_a_record():
    """Every cache entry must carry the record it was grounded from (so this gate can check it,
    and so provenance is auditable)."""
    for key, entry in _load().items():
        assert "explanation" in entry and entry["explanation"].strip(), f"{key}: empty explanation"
        assert "grounded_from" in entry, f"{key}: no grounding record attached"


@pytest.mark.parametrize("case", list(_load().items()) if os.path.exists(_CACHE) else [],
                         ids=lambda c: c[0] if isinstance(c, tuple) else str(c))
def test_explanation_speaks_experimentalist_voice(case):
    """EXPERIMENTALIST VOICE (Option B, 2026-07-11): the prose is written for a bench biologist,
    so it must NOT quote a statistic at all — z-scores, log-fold-changes, p/adj-p, q, and FDR
    belong in the figure beside the text, not in the sentence. This is stricter than "invents no
    numbers": even a CORRECT z-score in the prose now fails, because a stat readout is the wrong
    register for this reader. (Bare name digits like LCP2 / CD3 / 48-hour are scrubbed by
    `_prose_numbers`, so they don't trip this.)"""
    key, entry = case
    text = entry["explanation"]
    stat_numbers = [n for n in _prose_numbers(text)
                    if abs(n) >= 1e-12 and not (n == int(n) and abs(n) < 50)]
    assert not stat_numbers, (
        f"{key}: explanation quotes statistic(s) {sorted(stat_numbers)} in the prose — the "
        f"experimentalist voice keeps numbers in the figure, not the sentence. Text: {text!r}")
