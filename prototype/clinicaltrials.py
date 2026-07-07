"""
ClinicalTrials.gov live client (verifier Tier C — the "already in the clinic" beat).

PRE-KICKOFF PROTOTYPE. Not the hackathon submission; explores feasibility on
public data before the build officially starts. See prototype/README.md.

Why this exists: Open Targets' target->drug mapping (drugAndClinicalCandidates)
returns ZERO curated candidates for PTPN2 / CBLB / RASA2 — their inhibitors are
too new to be curated. But the trials are real and public. So the spotlight's
"already in Phase 1" claim is made MACHINE-VERIFIABLE by querying ClinicalTrials.gov
directly for the specific compound, returning a real NCT id + phase + status.

The tool no longer asserts "PTPN2 is in Phase 1" (unreproducible prose). It looks
up the named molecule live and reports whatever the registry says — if a trial
advances or is withdrawn, the report changes on re-run. It can't silently go stale.

API: ClinicalTrials.gov v2 (https://clinicaltrials.gov/api/v2/studies) — free, no key.
Results cached to ct_cache.json so re-runs are instant and offline-reproducible.
"""
import json
import os
import time
import urllib.parse
import urllib.request

BASE = "https://clinicaltrials.gov/api/v2/studies"
CACHE = os.path.join(os.path.dirname(__file__), "ct_cache.json")

# Order best-first so we can pick the most advanced phase a compound has reached.
PHASE_ORDER = ["PHASE4", "PHASE3", "PHASE2_3", "PHASE2", "PHASE1_2", "PHASE1", "EARLY_PHASE1"]
_PHASE_LABEL = {
    "PHASE4": "Phase 4", "PHASE3": "Phase 3", "PHASE2_3": "Phase 2/3",
    "PHASE2": "Phase 2", "PHASE1_2": "Phase 1/2", "PHASE1": "Phase 1",
    "EARLY_PHASE1": "Early Phase 1", "NA": "Not Applicable",
}


def _load_cache():
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    return {}


def _save_cache(c):
    json.dump(c, open(CACHE, "w"), indent=0)


def _query(intervention):
    qs = urllib.parse.urlencode({
        "query.intr": intervention,
        "pageSize": 20,
        "fields": "NCTId,BriefTitle,Phase,OverallStatus,InterventionName",
    })
    req = urllib.request.Request(BASE + "?" + qs, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _best_phase(phases):
    """Given a set of phase codes across trials, return the most advanced one."""
    for p in PHASE_ORDER:
        if p in phases:
            return p
    return "NA"


def _parse(data, intervention):
    """Reduce the studies list to the single most-advanced trial for this compound."""
    studies = data.get("studies", []) if data else []
    phases, best = set(), None
    for s in studies:
        p = s.get("protocolSection", {})
        idm = p.get("identificationModule", {})
        dm = p.get("designModule", {})
        sm = p.get("statusModule", {})
        study_phases = dm.get("phases") or []
        phases.update(study_phases)
        cand = {
            "nct_id": idm.get("nctId"),
            "title": (idm.get("briefTitle") or "")[:120],
            "status": sm.get("overallStatus"),
            "phases": study_phases,
        }
        # prefer a study that carries an explicit phase; keep the first such
        if best is None or (not best.get("phases") and study_phases):
            best = cand

    if not studies or best is None:
        return {"intervention": intervention, "found": False,
                "phase": None, "phase_label": None, "nct_id": None,
                "status": None, "title": None, "n_studies": 0}

    top = _best_phase(phases)
    return {
        "intervention": intervention,
        "found": True,
        "phase": top,
        "phase_label": _PHASE_LABEL.get(top, top),
        "nct_id": best["nct_id"],
        "status": best["status"],
        "title": best["title"],
        "n_studies": len(studies),
    }


def lookup(intervention, cache=None):
    """Return the trial-status dict for a compound (cached). Never raises: on any
    network/parse failure returns found=False with an 'error' note so the report
    can degrade to 'could not verify live' rather than crash."""
    cache = _load_cache() if cache is None else cache
    if intervention in cache:
        return cache[intervention]
    try:
        result = _parse(_query(intervention), intervention)
    except Exception as e:
        result = {"intervention": intervention, "found": False, "phase": None,
                  "phase_label": None, "nct_id": None, "status": None,
                  "title": None, "n_studies": 0, "error": str(e)}
    cache[intervention] = result
    _save_cache(cache)
    time.sleep(0.2)
    return result


if __name__ == "__main__":
    cache = _load_cache()
    print(f"{'compound':<16}{'found':<7}{'phase':<14}{'status':<14}nct")
    print("-" * 68)
    for intr in ["ABBV-CLS-484", "NX-1607", "tofacitinib", "not-a-real-drug-xyz"]:
        r = lookup(intr, cache)
        print(f"{intr:<16}{str(r['found']):<7}{str(r['phase_label']):<14}"
              f"{str(r['status']):<14}{r['nct_id']}")
