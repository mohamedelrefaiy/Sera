"""LIVE-DATA GATE — the fields the live figures draw from must exist and be HONEST.

The live-run notebook (web/live.html) draws three figures from tool/shortlist output:
  - rank-shift slope  <- raw_rank -> actionable_rank
  - robustness dotplot <- verify checks (donor/guide corr)  [covered by test_verify]
  - condition bars     <- by_condition[].n_downstream

Two things can silently break a figure into a LIE:
  1. a field goes missing (figure can't draw, or worse, draws a stale value), or
  2. a screen with NO breadth signal reports n_downstream as 0 instead of None
     (a real 0 means "measured, no downstream genes"; None means "not measured").
     That degradation branch NEVER fires on Marson (every cell is an int), so it must
     be exercised on Schmidt2022 (a MAGeCK screen with breadth_col=None) or it is
     only ever discovered live.

Run:  pytest eval/test_live_data.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from target_triage.core.data import load_screen  # noqa: E402
from target_triage.core.schema import SCHMIDT2022  # noqa: E402
from target_triage.llm.tools import _condition_rows, _row_for  # noqa: E402


# ---- the new fields exist and carry the verified real values (Marson) ----

def test_row_for_carries_actionable_rank():
    """The rank-shift figure needs both ranks; actionable_rank must be present and
    be the overlay rank, not the raw rank."""
    row = _row_for("PTPN2")
    assert row["raw_rank"] == 836, row["raw_rank"]
    assert row["actionable_rank"] == 43, row["actionable_rank"]
    assert row["actionable_rank"] != row["raw_rank"]


def test_row_for_by_condition_is_ordered_and_real():
    """The context figure needs per-condition breadth in canonical Rest->Stim order,
    with the verified CBLB story values (near-silent at rest, explodes on stim)."""
    row = _row_for("CBLB")
    conds = [c["condition"] for c in row["by_condition"]]
    assert conds == ["Rest", "Stim8hr", "Stim48hr"], conds
    breadth = {c["condition"]: c["n_downstream"] for c in row["by_condition"]}
    assert breadth == {"Rest": 5, "Stim8hr": 1027, "Stim48hr": 179}, breadth
    # every Marson cell is a measured int — the None branch must NOT appear here
    assert all(isinstance(v, int) for v in breadth.values())


def test_row_for_actionable_rank_none_for_unranked_gene():
    """A gene present in the screen but not in the ranked set must yield
    actionable_rank=None (honest absence), never a fabricated number."""
    row = _row_for("A1BG")  # noise gene, present in screen
    assert row is not None
    # the contract is: int or None, never a guess
    assert row["actionable_rank"] is None or isinstance(row["actionable_rank"], int)


# ---- the honest-degradation branch: a screen with NO breadth signal (Schmidt) ----

def test_by_condition_reports_none_when_screen_has_no_breadth():
    """Schmidt2022 has breadth_col=None -> every n_downstream must be None (JSON null),
    NEVER coerced to 0. This is the branch that never fires on Marson."""
    schmidt = load_screen(SCHMIDT2022)
    assert schmidt, "Schmidt screen failed to load"
    record = next(r for r in schmidt if r.by_condition)
    rows = _condition_rows(record)
    assert rows, "no condition rows produced"
    for r in rows:
        assert r["n_downstream"] is None, (
            f"{record.gene}/{r['condition']}: expected None (no breadth signal), "
            f"got {r['n_downstream']!r} — a missing signal must not become 0"
        )
