"""
Open Targets overlay (SCRATCHPAD sketch, not committed project code).

Replaces the hand-coded DRUGGABLE/DISEASE stub sets in rank_sketch.py with a
LIVE query to the Open Targets GraphQL API (free, no key). For each gene it returns:
  - druggable_score : 0..1  from the tractability buckets (does a drug / ligand / druggable family exist?)
  - disease_score   : 0..1  best genetic_association score across its top diseases (autoimmune GWAS signal)
  - top_disease     : name of that best-genetic-association disease
  - clinical        : highest tractability stage reached ("Approved"/"Advanced clinical"/"Phase 1"/None)

Verified against the live schema 2026-07-07:
  Target.tractability      -> [{modality: SM|AB|PR, value: bool, label: str}]
  Target.associatedDiseases-> rows[].datatypeScores[] with id == "genetic_association"

Results are cached to ot_cache.json so re-runs don't re-hit the API.
"""
import json, os, time, urllib.request

API = "https://api.platform.opentargets.org/api/v4/graphql"
CACHE = "ot_cache.json"

QUERY = """
query($id:String!){
  target(ensemblId:$id){
    approvedSymbol
    tractability { modality value label }
    associatedDiseases(page:{index:0,size:25}){
      rows { disease{name therapeuticAreas{name}} datatypeScores{ id score } }
    }
  }
}
"""

# We only care about disease genetics that are IMMUNE/inflammatory — a gene that
# causes a rare developmental syndrome (high "genetic_association" in OT) is NOT a
# relevant T-cell drug target. Filter disease rows by therapeutic area / name.
IMMUNE_AREAS = {
    "immune system disease", "infectious disease",
}
IMMUNE_TERMS = (
    "autoimmun", "arthritis", "lupus", "psoriasis", "colitis", "crohn",
    "inflammatory bowel", "diabetes mellitus, type 1", "type 1 diabetes",
    "multiple sclerosis", "vitiligo", "celiac", "asthma", "allerg",
    "immunodeficiency", "inflammat", "eczema", "atopic", "ankylosing",
    "sclerosis", "graves", "thyroiditis", "vasculitis", "immune",
)


def _is_immune(disease):
    name = (disease.get("name") or "").lower()
    areas = {a["name"].lower() for a in (disease.get("therapeuticAreas") or [])}
    if areas & IMMUNE_AREAS:
        return True
    return any(t in name for t in IMMUNE_TERMS)

# tractability labels that imply real clinical progress, best-first
CLINICAL_LADDER = ["Approved Drug", "Advanced Clinical", "Phase 1 Clinical"]


def _post(ensembl_id):
    body = json.dumps({"query": QUERY, "variables": {"id": ensembl_id}}).encode()
    req = urllib.request.Request(API, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _load_cache():
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    return {}


def _save_cache(c):
    json.dump(c, open(CACHE, "w"), indent=0)


def _parse(data, symbol=None):
    """Turn one target() GraphQL object into our annotation dict."""
    if data is None:                       # gene not in Open Targets
        return {"symbol": symbol, "druggable_score": 0.0, "disease_score": 0.0,
                "top_disease": None, "clinical": None, "note": "not in OT"}

    tract = data.get("tractability") or []
    # druggable_score: fraction of "does a real handle exist?" signals that are true,
    # for the small-molecule (SM) modality — the relevant one for these enzymes.
    sm = [t for t in tract if t["modality"] == "SM"]
    key_labels = ["Approved Drug", "Advanced Clinical", "Phase 1 Clinical",
                  "High-Quality Ligand", "Structure with Ligand", "Druggable Family"]
    hits = [t for t in sm if t["label"] in key_labels and t["value"]]
    druggable_score = round(len(hits) / len(key_labels), 3)

    # clinical stage = best rung on the ladder that is true (any modality)
    clinical = None
    for rung in CLINICAL_LADDER:
        if any(t["label"] == rung and t["value"] for t in tract):
            clinical = rung.replace(" Clinical", "").replace(" Drug", "")
            break

    # disease_score: best genetic_association across IMMUNE-relevant diseases only.
    # (A high score for a rare developmental syndrome is not a T-cell drug rationale.)
    best_ga, best_dis = 0.0, None
    for row in (data.get("associatedDiseases") or {}).get("rows", []):
        if not _is_immune(row.get("disease", {})):
            continue
        for s in row.get("datatypeScores", []):
            if s["id"] == "genetic_association" and s["score"] > best_ga:
                best_ga, best_dis = s["score"], row["disease"]["name"]

    return {
        "symbol": data.get("approvedSymbol") or symbol,
        "druggable_score": druggable_score,
        "disease_score": round(best_ga, 3),
        "top_disease": best_dis,
        "clinical": clinical,
    }


def annotate(ensembl_id, symbol=None, cache=None):
    """Return the Open Targets annotation dict for ONE gene (cached)."""
    cache = _load_cache() if cache is None else cache
    if ensembl_id in cache:
        return cache[ensembl_id]
    try:
        result = _parse(_post(ensembl_id)["data"]["target"], symbol)
    except Exception as e:
        result = {"symbol": symbol, "error": str(e), "druggable_score": 0.0,
                  "disease_score": 0.0, "top_disease": None, "clinical": None}
    cache[ensembl_id] = result
    _save_cache(cache)
    time.sleep(0.2)
    return result


def _post_batch(id_map):
    """id_map = {alias: ensembl_id}. One GraphQL request, many aliased target()s."""
    fields = ("approvedSymbol tractability{modality value label} "
              "associatedDiseases(page:{index:0,size:25}){rows{"
              "disease{name therapeuticAreas{name}} datatypeScores{id score}}}")
    parts = [f'{alias}: target(ensemblId:"{eid}"){{{fields}}}'
             for alias, eid in id_map.items()]
    body = json.dumps({"query": "{" + " ".join(parts) + "}"}).encode()
    req = urllib.request.Request(API, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read()).get("data", {}) or {}


def batch_annotate(pairs, chunk=40, progress=None):
    """Annotate many genes. pairs = [(symbol, ensembl_id), ...]. Cached + batched.
    Returns {symbol: annotation}. Only un-cached genes hit the API."""
    cache = _load_cache()
    out, todo = {}, []
    for sym, eid in pairs:
        if eid in cache:
            out[sym] = cache[eid]
        else:
            todo.append((sym, eid))

    for i in range(0, len(todo), chunk):
        batch = todo[i:i + chunk]
        id_map = {f"g{j}": eid for j, (_, eid) in enumerate(batch)}
        try:
            data = _post_batch(id_map)
        except Exception as e:
            data = {}
            if progress:
                progress(f"  batch {i//chunk} error: {e}")
        for j, (sym, eid) in enumerate(batch):
            ann = _parse(data.get(f"g{j}"), sym)
            cache[eid] = ann
            out[sym] = ann
        _save_cache(cache)
        if progress:
            progress(f"  annotated {min(i+chunk, len(todo))}/{len(todo)} new genes")
        time.sleep(0.2)
    return out


if __name__ == "__main__":
    # quick self-test on our anchors
    tests = {"PTPN2": "ENSG00000175354", "CBLB": "ENSG00000114423",
             "TNFAIP3": "ENSG00000118503", "RASA2": "ENSG00000155903",
             "CD3E": "ENSG00000198851"}
    cache = _load_cache()
    print(f"{'gene':<9}{'drug':>6}{'dis':>6}  {'clinical':<10} top_disease")
    print("-" * 60)
    for sym, eid in tests.items():
        a = annotate(eid, sym, cache)
        print(f"{sym:<9}{a['druggable_score']:>6.2f}{a['disease_score']:>6.2f}  "
              f"{str(a['clinical']):<10} {a['top_disease']}")
