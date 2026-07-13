"""API SCREEN GATE — the reusability proof must be reachable from the product.

Until now `core/` was screen-agnostic but every deterministic endpoint called
`load_perturbations()`, which ignores its own `path` argument and always returns
Marson. The instrument was portable in tests and Marson-only in the browser.

This gate asserts the screen reaches the API: every deterministic route takes
`?screen=`, serves that screen's own data, and rejects an unknown one. It also
asserts the two properties that make the picker HONEST:

  - the controls gate is exposed, so the UI can show PASS before any novel pick;
  - each screen's volcano declares the axes it can actually plot (ADR-0001),
    rather than a breadth axis a MAGeCK screen cannot fill.

Run:  pytest eval/test_api_screens.py
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sera.api.app import app  # noqa: E402

SCREENS = ("marson", "schmidt2022")


@pytest.fixture(scope="module")
def client():
    # lifespan precomputes every registered screen's shortlist
    with TestClient(app) as c:
        yield c


# ---- the screen reaches every deterministic route ----


@pytest.mark.parametrize("screen", SCREENS)
def test_shortlist_serves_each_screen(client, screen):
    r = client.get(f"/api/shortlist?screen={screen}")
    assert r.status_code == 200
    body = r.json()
    assert body["screen"] == screen
    assert body["total"] > 0
    assert body["rows"], "a screen that passes its controls gate must rank something"


def test_shortlists_differ_between_screens(client):
    """The proof: same instrument, genuinely different output."""
    marson = client.get("/api/shortlist?screen=marson").json()
    schmidt = client.get("/api/shortlist?screen=schmidt2022").json()
    assert marson["total"] != schmidt["total"]
    assert marson["rows"][0]["gene"] != schmidt["rows"][0]["gene"]


def test_shortlist_defaults_to_marson(client):
    """No ?screen= must keep the existing contract for every current caller."""
    assert client.get("/api/shortlist").json()["screen"] == "marson"


@pytest.mark.parametrize("route", ["/api/shortlist", "/api/funnel", "/api/volcano"])
def test_unknown_screen_is_a_404_not_a_silent_marson(client, route):
    r = client.get(f"{route}?screen=nonesuch")
    assert r.status_code == 404
    assert "nonesuch" in r.json()["detail"]


def test_target_is_scoped_to_its_screen(client):
    """VAV1 is a Schmidt hit; PTPN2 is a Marson spotlight gene. Neither may leak."""
    assert client.get("/api/target/VAV1?screen=schmidt2022").status_code == 200
    assert client.get("/api/target/PTPN2?screen=schmidt2022").status_code == 404
    assert client.get("/api/target/PTPN2?screen=marson").status_code == 200


@pytest.mark.parametrize("screen", SCREENS)
def test_funnel_counts_come_from_the_selected_screen(client, screen):
    body = client.get(f"/api/funnel?screen={screen}").json()
    assert body["screen"] == screen
    assert body["loaded_genes"] > 0
    assert body["significant"] <= body["loaded_genes"]


# ---- the picker is honest: controls up front, axes declared ----


@pytest.mark.parametrize("screen", SCREENS)
def test_controls_gate_is_exposed_per_screen(client, screen):
    body = client.get(f"/api/controls?screen={screen}").json()
    assert body["screen"] == screen
    assert body["passed"] is True, f"{screen}: {body['summary']}"
    assert len(body["results"]) == 5
    assert all(r["passed"] for r in body["results"])


def test_screens_route_lists_the_registry(client):
    body = client.get("/api/screens").json()
    names = {s["name"] for s in body["screens"]}
    assert names == set(SCREENS)
    for s in body["screens"]:
        assert s["description"]
        assert s["impact_axis"] in ("breadth", "neg_log10_fdr")


def _points(body: dict) -> list[dict]:
    """Every plotted point. Note `background` is empty on Marson today: compute_shortlist
    returns every significant gene, so all 7195 are labelled and none fall through to the
    anonymous cloud. Assert over both lists rather than assuming where a point lands."""
    return body["background"] + body["labelled"]


def test_volcano_declares_the_axis_each_screen_can_plot(client):
    marson = client.get("/api/volcano?screen=marson").json()
    assert marson["impact_axis"] == "breadth"
    assert marson["y_label"] == "downstream genes moved"
    assert any(p["impact"] is not None for p in _points(marson))

    schmidt = client.get("/api/volcano?screen=schmidt2022").json()
    assert schmidt["impact_axis"] == "neg_log10_fdr"
    assert schmidt["y_label"] == "−log10(FDR)"
    assert any(p["impact"] is not None for p in _points(schmidt))


def test_volcano_never_fabricates_a_missing_axis(client):
    """Schmidt has no breadth. Its y must be -log10(FDR), never a zero-filled breadth."""
    schmidt = client.get("/api/volcano?screen=schmidt2022").json()
    pts = _points(schmidt)
    assert pts
    assert all(p["downstream"] is None for p in pts), "Schmidt has no breadth to report"
    assert all(p["impact"] is not None for p in pts), "every FDR must yield an axis value"
    # Significance is fdr < 0.10, so -log10(fdr) > 1.0 exactly. The payload rounds to
    # 3dp, and a borderline hit (TAF9B, fdr=0.099935 -> 1.00028) rounds down to 1.0.
    # Assert the post-rounding bound; asserting > 1.0 would test the rounding, not the data.
    assert min(p["impact"] for p in pts) >= 1.0


def test_schmidt_impact_is_really_derived_from_the_fdr():
    """The rounded payload can't prove the axis is real. Assert against the loader:
    every significant Schmidt hit has fdr < fdr_max, so -log10(fdr) strictly exceeds 1."""
    import math

    from sera.core.data import load_screen
    from sera.core.ranking import significant_records
    from sera.core.schema import SCHMIDT2022

    checked = 0
    for rec in significant_records(load_screen(SCHMIDT2022)):
        for p in rec.by_condition.values():
            if p.significant and not p.offtarget:
                assert p.fdr is not None and p.fdr < SCHMIDT2022.fdr_max
                assert -math.log10(p.fdr) > 1.0
                checked += 1
    assert checked > 100, "expected a real population of significant hits"


def test_marson_zero_breadth_is_a_measurement_not_an_absence(client):
    """A knockdown that moved 0 downstream genes reported 0 — distinct from a screen
    that reports no breadth at all. The axis must carry 0.0, never None, and the
    frontend must therefore use log1p (not log) for the breadth scale."""
    marson = client.get("/api/volcano?screen=marson").json()
    pts = _points(marson)
    assert all(p["impact"] is not None for p in pts), "Marson reports breadth for every hit"
    assert any(p["impact"] == 0.0 for p in pts), "genes with zero breadth exist"
    assert marson["y_scale"] == "log1p", "0 breadth is real; log(0) is not"


# ---- Agent Activity rail + Sources: the peripheral-agent layer surfaced in the UI -------------


def test_agent_activity_is_a_view_of_the_real_receipt(client):
    """The rail's timeline must be derived from the provenance artifact, not scripted. Every event
    ties back to a screen mapping or a cited claim, and the Freimer sign-correction — a real event
    on real data — must be present when the receipt is built."""
    d = client.get("/api/agent_activity").json()
    if not d["built"]:
        pytest.skip("provenance.json not built — run pipeline/06_ingest.py")
    assert d["events"], "a built receipt must yield events"
    assert all(e["node"] in ("A", "B") for e in d["events"]), "every event is Node A or Node B"
    titles = [e["title"] for e in d["events"]]
    assert any("sign corrected" in t for t in titles), (
        "the Freimer sign-correction is a real event and must appear in the rail")
    assert any(e["node"] == "B" for e in d["events"]), "Node B citations must appear too"


def test_sources_only_returns_cited_claims_and_is_honest_when_empty(client):
    """The Sources section shows Node B's cited hypotheses for a gene. A gene Node B interpreted
    (TSC1, discordant) returns cited claims with resolvable PMIDs; a replicated gene (ITK) returns
    an empty list — never a fabricated mechanism to fill the space."""
    if not client.get("/api/agent_activity").json()["built"]:
        pytest.skip("provenance.json not built — run pipeline/06_ingest.py")

    for c in client.get("/api/sources/TSC1").json()["claims"]:
        assert c["citation"]["accession"], "a cited claim must carry an accession"
        assert c["citation"]["url"].startswith("https://pubmed."), "citation must link out"
        assert "not used in verdict" in c["label"], "every hypothesis is labelled"

    itk = client.get("/api/sources/ITK").json()
    assert itk["claims"] == [], "a replicated gene has no hypotheses — the section must be empty"
