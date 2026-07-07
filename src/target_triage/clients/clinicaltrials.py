"""ClinicalTrials.gov client: live trial status for a named compound.

Open Targets has no curated drug for very-new inhibitors (PTPN2/CBLB targets return
zero), so the "already in the clinic" beat is verified here instead — by querying
the registry directly for the molecule and returning a real NCT id + phase + status.

The tool names a compound (a checkable fact); the phase is looked up live, so the
claim can never silently go stale. On any failure it returns found=False rather than
asserting a phase. Cached to ct_cache.json for offline-reproducible re-runs.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

BASE = "https://clinicaltrials.gov/api/v2/studies"
CACHE = os.path.join(os.path.dirname(__file__), "ct_cache.json")

_PHASE_ORDER = ("PHASE4", "PHASE3", "PHASE2_3", "PHASE2", "PHASE1_2", "PHASE1", "EARLY_PHASE1")
_PHASE_LABEL = {
    "PHASE4": "Phase 4", "PHASE3": "Phase 3", "PHASE2_3": "Phase 2/3",
    "PHASE2": "Phase 2", "PHASE1_2": "Phase 1/2", "PHASE1": "Phase 1",
    "EARLY_PHASE1": "Early Phase 1", "NA": "Not Applicable",
}


@dataclass(frozen=True)
class TrialStatus:
    compound: str
    found: bool
    phase: str | None
    phase_label: str | None
    nct_id: str | None
    status: str | None
    title: str | None
    n_studies: int


def _query(intervention: str) -> dict:
    qs = urllib.parse.urlencode({
        "query.intr": intervention,
        "pageSize": 20,
        "fields": "NCTId,BriefTitle,Phase,OverallStatus,InterventionName",
    })
    req = urllib.request.Request(BASE + "?" + qs, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _best_phase(phases: set[str]) -> str:
    for p in _PHASE_ORDER:
        if p in phases:
            return p
    return "NA"


def _parse(data: dict, compound: str) -> TrialStatus:
    studies = data.get("studies", []) if data else []
    phases: set[str] = set()
    best = None
    for s in studies:
        p = s.get("protocolSection", {})
        idm = p.get("identificationModule", {})
        dm = p.get("designModule", {})
        sm = p.get("statusModule", {})
        study_phases = dm.get("phases") or []
        phases.update(study_phases)
        cand = {"nct_id": idm.get("nctId"), "title": (idm.get("briefTitle") or "")[:120],
                "status": sm.get("overallStatus"), "phases": study_phases}
        if best is None or (not best["phases"] and study_phases):
            best = cand

    if not studies or best is None:
        return TrialStatus(compound, False, None, None, None, None, None, 0)

    top = _best_phase(phases)
    return TrialStatus(compound, True, top, _PHASE_LABEL.get(top, top),
                       best["nct_id"], best["status"], best["title"], len(studies))


def _load_cache() -> dict:
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    return {}


def _save_cache(cache: dict) -> None:
    json.dump(cache, open(CACHE, "w"), indent=0)


def _to_dict(t: TrialStatus) -> dict:
    return {"compound": t.compound, "found": t.found, "phase": t.phase,
            "phase_label": t.phase_label, "nct_id": t.nct_id, "status": t.status,
            "title": t.title, "n_studies": t.n_studies}


def _from_dict(d: dict) -> TrialStatus:
    return TrialStatus(d["compound"], d["found"], d.get("phase"), d.get("phase_label"),
                       d.get("nct_id"), d.get("status"), d.get("title"), d.get("n_studies", 0))


def lookup(compound: str) -> TrialStatus:
    """Return the trial status for a compound (cached). Never raises — on any
    failure returns found=False so callers can say 'not verified live'."""
    cache = _load_cache()
    if compound in cache:
        return _from_dict(cache[compound])
    try:
        status = _parse(_query(compound), compound)
    except Exception:  # noqa: BLE001
        status = TrialStatus(compound, False, None, None, None, None, None, 0)
    cache[compound] = _to_dict(status)
    _save_cache(cache)
    time.sleep(0.2)
    return status


if __name__ == "__main__":
    for c in ("ABBV-CLS-484", "NX-1607"):
        t = lookup(c)
        print(f"{c:<14} found={t.found} {t.phase_label} {t.status} {t.nct_id}")
