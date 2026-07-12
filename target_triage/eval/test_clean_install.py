"""CLEAN-INSTALL + DEMO-PATH SMOKE GATE — the submission must not be dead on arrival.

Two failure modes this gate catches, both of which pass locally (the dev .venv happens to have
pandas + the artifacts on disk) but would break a fresh `pip install -e application/`:

  1. Runtime deps: api/app.py and llm/concord_tools.py read concordance.parquet with pandas at
     request time. If pandas / pyarrow live only in an optional extra, a clean install boots but
     503s on the first gene view. So they MUST be in base [project.dependencies].
  2. Package data: the deterministic artifacts (parquet / json / yaml) are not .py, so setuptools
     drops them unless [tool.setuptools.package-data] lists their patterns. A clean install without
     them 503s too.

Then it drives the actual demo path (the TSC1 story a judge will click through) end to end, so a
regression in any of it fails here rather than on stage.

Run:  pytest eval/test_clean_install.py
"""
from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from target_triage.api.app import app  # noqa: E402

_PYPROJECT = os.path.join(os.path.dirname(__file__), "..", "..", "pyproject.toml")


def _pyproject() -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:                       # py<3.11 fallback
        import tomli as tomllib
    with open(_PYPROJECT, "rb") as fh:
        return tomllib.load(fh)


# --- packaging declarations that make a clean install actually run ---------------------------

def test_pandas_and_pyarrow_are_runtime_dependencies_not_an_extra():
    deps = " ".join(_pyproject()["project"]["dependencies"]).lower()
    assert "pandas" in deps, "pandas must be a RUNTIME dep — the app reads parquet at request time"
    assert "pyarrow" in deps, "pyarrow (parquet engine) must be a RUNTIME dep"


def test_artifacts_ship_as_package_data():
    patterns = _pyproject()["tool"]["setuptools"]["package-data"]["target_triage"]
    joined = " ".join(patterns)
    # the served app reads parquet + json + yaml artifacts; all three must be packaged
    assert any(p.endswith("*.parquet") for p in patterns), "parquet artifacts not in package-data"
    assert any(p.endswith("*.json") and "data" in p for p in patterns), "json artifacts not packaged"
    assert any(p.endswith("*.yaml") for p in patterns), "yaml artifacts not in package-data"
    assert "frontend/**/*" in joined, "the served frontend must ship with the install"


def test_runtime_can_actually_read_the_parquet():
    """Not just declared — importable. If pyarrow/pandas were missing this raises at read time."""
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401


# --- the demo path a judge clicks through -----------------------------------------------------

def test_demo_path_serves_end_to_end():
    with TestClient(app) as client:
        # 1. the app boots and serves the frontend
        assert client.get("/").status_code == 200
        # 2. known-biology validation panel (the trust proof) is reachable
        gt = client.get("/api/ground_truth")
        assert gt.status_code == 200
        # 3. the flagship gene resolves to a full, honest decision brief
        r = client.get("/api/concordance/TSC1")
        assert r.status_code == 200
        b = r.json()["decision_brief"]
        assert b is not None, "the demo hinges on TSC1's brief — it must not be null"
        assert b["snapshot"]["verdict"] == "discordant"
        assert b["comparability"] == "partially_comparable"
        assert b["explanations"] and b["outcome_matrix"] and b["citations"]
        # 4. the adaptive constraint beat: an infeasible ask is reported, not silently downgraded
        infeasible = client.get(
            "/api/concordance/TSC1?readouts=elisa&donors=2&days=2").json()["decision_brief"]
        assert infeasible["feasible"] is False and infeasible["unmet_requirements"]


def test_demo_gene_is_not_a_fabrication_when_absent():
    with TestClient(app) as client:
        assert client.get("/api/concordance/NOTAGENE12345").status_code == 404
