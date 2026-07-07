"""AGENT TOOL-LAYER GATE — the SDK tools must register and execute correctly.

Structural + behavioral check on the agent's tools WITHOUT an API call: the server
builds, all tools register, and each tool's handler returns the right computed facts.
This proves the Claude-facing surface works before any (paid) full agent run.

Async handlers are driven with asyncio.run() so no pytest async plugin is needed.

Run:  pytest eval/test_tools.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from target_triage import tools  # noqa: E402
from target_triage.agent import build_options  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _payload(result):
    return json.loads(result["content"][0]["text"])


def test_server_builds_and_registers_tools():
    build_options()  # must not raise
    assert len(tools.ALLOWED_TOOLS) == 7
    names = {t.name for t in (
        tools.rank_candidates, tools.verify_candidate, tools.check_open_targets,
        tools.check_clinical_trials, tools.set_view, tools.focus_gene, tools.reverify)}
    assert names == {"rank_candidates", "verify_candidate", "check_open_targets",
                     "check_clinical_trials", "set_view", "focus_gene", "reverify"}


def test_set_view_emits_view_update():
    out = _payload(_run(tools.set_view.handler(
        {"condition": "Rest", "min_druggable": 0, "promoted_only": True})))
    vu = out["__view_update__"]
    assert vu["action"] == "set_rows" and len(vu["rows"]) > 0
    assert all(r["best_condition"] == "Rest" for r in vu["rows"])
    assert all(r["verdict"].startswith("PROMOTE") for r in vu["rows"])


def test_focus_gene_pulls_any_gene():
    out = _payload(_run(tools.focus_gene.handler({"gene": "PTPN2"})))
    assert out["__view_update__"]["action"] == "focus"
    assert out["__view_update__"]["gene"] == "PTPN2"
    assert out["verdict"].startswith("PROMOTE")


def test_reverify_with_strict_threshold_flips_verdict():
    # CBLB is PROMOTE at default donor floor (0.10); its donor corr is 0.75, so a
    # 0.80 floor must flip it to REJECT — the scientist's stress-test interaction.
    default = _payload(_run(tools.verify_candidate.handler({"gene": "CBLB"})))
    strict = _payload(_run(tools.reverify.handler({"gene": "CBLB", "min_donor_corr": 0.8})))
    assert default["verdict"].startswith("PROMOTE")
    assert strict["verdict"] == "REJECT"
    assert strict["__view_update__"]["action"] == "focus"


def test_rank_candidates_runs():
    out = _payload(_run(tools.rank_candidates.handler({"top_n": 5})))
    assert out["total_ranked"] > 5000
    assert len(out["candidates"]) == 5
    assert "is_obvious_tcr_machinery" in out["candidates"][0]


def test_verify_tool_matches_verdicts():
    cblb = _payload(_run(tools.verify_candidate.handler({"gene": "CBLB"})))
    a1bg = _payload(_run(tools.verify_candidate.handler({"gene": "A1BG"})))
    assert cblb["verdict"].startswith("PROMOTE")
    assert a1bg["verdict"] == "REJECT"


def test_external_tools_return_cached_values():
    ot = _payload(_run(tools.check_open_targets.handler({"gene": "CBLB"})))
    assert ot["druggable_score"] > 0 and ot["top_immune_disease"]
    ct = _payload(_run(tools.check_clinical_trials.handler({"compound": "NX-1607"})))
    assert ct["found"] and ct["nct_id"] == "NCT05107674"
