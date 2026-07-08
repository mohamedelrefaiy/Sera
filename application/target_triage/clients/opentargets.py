"""Open Targets client: druggability + immune-disease genetics for a target.

Live GraphQL API (free, no key). For each Ensembl id returns an immutable
OpenTargetsAnnotation:
  - druggable_score : 0..1 from small-molecule tractability buckets
  - disease_score   : 0..1 best genetic-association across IMMUNE-relevant diseases
  - top_disease     : that disease's name
  - clinical_stage  : best tractability clinical rung, if any

Immune filtering uses a negation guard FIRST (so "non-immune hydrops fetalis" and
ALS/tuberous-sclerosis names never count), then therapeutic-area + term matching.
Results cache to ot_cache.json for instant, offline-reproducible re-runs.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass

API = "https://api.platform.opentargets.org/api/v4/graphql"
CACHE = os.path.join(os.path.dirname(__file__), "ot_cache.json")

IMMUNE_AREAS = {"immune system disease", "infectious disease"}
IMMUNE_TERMS = (
    "autoimmun", "arthritis", "lupus", "psoriasis", "colitis", "crohn",
    "inflammatory bowel", "diabetes mellitus, type 1", "type 1 diabetes",
    "multiple sclerosis", "systemic sclerosis", "vitiligo", "celiac", "asthma",
    "allerg", "immunodeficiency", "immunodeficien", "autoinflammat", "inflammat",
    "eczema", "atopic", "ankylosing", "graves", "thyroiditis", "vasculitis",
    "sjogren", "scleroderma", "uveitis", "spondyl", "myasthenia gravis",
)
NON_IMMUNE_EXCLUDE = (
    "non-immune", "nonimmune", "hydrops fetalis",
    "amyotrophic lateral sclerosis", "tuberous sclerosis", "hippocampal sclerosis",
    "noonan", "leopard syndrome", "sotos", "cardiofaciocutaneous",
)
CLINICAL_LADDER = ("Approved Drug", "Advanced Clinical", "Phase 1 Clinical")
_SM_KEY_LABELS = (
    "Approved Drug", "Advanced Clinical", "Phase 1 Clinical",
    "High-Quality Ligand", "Structure with Ligand", "Druggable Family",
)

_BATCH_FIELDS = (
    "approvedSymbol tractability{modality value label} "
    "associatedDiseases(page:{index:0,size:25}){rows{"
    "disease{name therapeuticAreas{name}} datatypeScores{id score}}}"
)


@dataclass(frozen=True)
class OpenTargetsAnnotation:
    symbol: str | None
    druggable_score: float
    disease_score: float
    top_disease: str | None
    clinical_stage: str | None


def _is_immune(disease: dict) -> bool:
    name = (disease.get("name") or "").lower()
    if any(x in name for x in NON_IMMUNE_EXCLUDE):
        return False
    areas = {a["name"].lower() for a in (disease.get("therapeuticAreas") or [])}
    if areas & IMMUNE_AREAS:
        return True
    return any(t in name for t in IMMUNE_TERMS)


def _parse(target: dict | None, symbol: str | None) -> OpenTargetsAnnotation:
    if target is None:
        return OpenTargetsAnnotation(symbol, 0.0, 0.0, None, None)

    tract = target.get("tractability") or []
    sm = [t for t in tract if t["modality"] == "SM"]
    hits = [t for t in sm if t["label"] in _SM_KEY_LABELS and t["value"]]
    druggable = round(len(hits) / len(_SM_KEY_LABELS), 3)

    clinical = None
    for rung in CLINICAL_LADDER:
        if any(t["label"] == rung and t["value"] for t in tract):
            clinical = rung.replace(" Clinical", "").replace(" Drug", "")
            break

    best_ga, best_dis = 0.0, None
    for row in (target.get("associatedDiseases") or {}).get("rows", []):
        if not _is_immune(row.get("disease", {})):
            continue
        for s in row.get("datatypeScores", []):
            if s["id"] == "genetic_association" and s["score"] > best_ga:
                best_ga, best_dis = s["score"], row["disease"]["name"]

    return OpenTargetsAnnotation(
        symbol=target.get("approvedSymbol") or symbol,
        druggable_score=druggable,
        disease_score=round(best_ga, 3),
        top_disease=best_dis,
        clinical_stage=clinical,
    )


def _load_cache() -> dict:
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    return {}


def _save_cache(cache: dict) -> None:
    json.dump(cache, open(CACHE, "w"), indent=0)


def _post_batch(id_map: dict[str, str]) -> dict:
    parts = [f'{alias}: target(ensemblId:"{eid}"){{{_BATCH_FIELDS}}}'
             for alias, eid in id_map.items()]
    body = json.dumps({"query": "{" + " ".join(parts) + "}"}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read()).get("data", {}) or {}


def _ann_to_dict(a: OpenTargetsAnnotation) -> dict:
    return {"symbol": a.symbol, "druggable_score": a.druggable_score,
            "disease_score": a.disease_score, "top_disease": a.top_disease,
            "clinical_stage": a.clinical_stage}


def _dict_to_ann(d: dict) -> OpenTargetsAnnotation:
    return OpenTargetsAnnotation(
        d.get("symbol"), d.get("druggable_score", 0.0), d.get("disease_score", 0.0),
        d.get("top_disease"), d.get("clinical_stage"))


def annotate_many(pairs, chunk: int = 40, progress=None) -> dict[str, OpenTargetsAnnotation]:
    """Annotate many genes. pairs = [(symbol, ensembl_id), ...]. Cached + batched.
    Returns {symbol: OpenTargetsAnnotation}. Only un-cached genes hit the API."""
    cache = _load_cache()
    out: dict[str, OpenTargetsAnnotation] = {}
    todo = []
    for sym, eid in pairs:
        if eid in cache:
            out[sym] = _dict_to_ann(cache[eid])
        else:
            todo.append((sym, eid))

    for i in range(0, len(todo), chunk):
        batch = todo[i:i + chunk]
        id_map = {f"g{j}": eid for j, (_, eid) in enumerate(batch)}
        try:
            data = _post_batch(id_map)
        except Exception as e:  # noqa: BLE001 — degrade to empty, never crash the run
            data = {}
            if progress:
                progress(f"  OT batch {i // chunk} error: {e}")
        for j, (sym, eid) in enumerate(batch):
            ann = _parse(data.get(f"g{j}"), sym)
            cache[eid] = _ann_to_dict(ann)
            out[sym] = ann
        _save_cache(cache)
        if progress:
            progress(f"  OT annotated {min(i + chunk, len(todo))}/{len(todo)} new genes")
        time.sleep(0.2)
    return out


if __name__ == "__main__":
    tests = [("PTPN2", "ENSG00000175354"), ("CBLB", "ENSG00000114423"),
             ("RASA2", "ENSG00000155903")]
    for sym, ann in annotate_many(tests).items():
        print(f"{sym:<8} drug={ann.druggable_score:.2f} dis={ann.disease_score:.2f} "
              f"clin={ann.clinical_stage} {ann.top_disease}")
