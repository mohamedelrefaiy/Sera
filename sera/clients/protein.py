"""Protein identity + structure client: the facts a mini-report needs, authored by CODE.

A "what do we know about this protein" panel is nothing BUT external facts — the UniProt
accession, the protein's name and length, the experimental structure that exists (or the
predicted model that stands in for it). Every one is exactly the kind of identifier the
interpretation node was caught fabricating (see rim/interpret.py: it once cited Q16558 for
TSC1, which is a potassium channel). So none of these may come from the model. This client
RETRIEVES them from the authoritative source and hands them down as data; the agent narrates
around them and never authors one.

Three live sources, all free/no-key, all disk-cached for offline-reproducible demo re-runs:

  - UniProt REST         gene symbol (human) -> reviewed accession, protein name, length, PDB xrefs
  - RCSB PDB search      that accession -> the best EXPERIMENTAL structure (lowest resolution Å)
  - AlphaFold DB         that accession -> the predicted model, used ONLY when no experiment exists

The resolution order encodes an honesty rule: an experimental structure is shown as experimental,
a predicted one is labelled `alphafold` and never dressed up as a solved structure. If everything
fails the client degrades to a null-but-well-formed record — the card then says "structure
unavailable", never invents a PDB id.

Mirror of clients/opentargets.py: a frozen dataclass, a json cache next to the module, and the
strict rule that a network failure returns empty rather than raising into the request path.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace

CACHE = os.path.join(os.path.dirname(__file__), "protein_cache.json")

_UNIPROT_SEARCH = "https://rest.uniprot.org/uniprotkb/search"
_RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
_ALPHAFOLD_API = "https://alphafold.ebi.ac.uk/api/prediction"

# The same official UniProt accession shape rim/interpret.py validates against. A retrieved
# accession is checked against it too — a source returning something off-pattern is treated as no
# result, never passed through as a fact.
_UNIPROT_RE = re.compile(
    r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})$")
# RCSB entry ids are a 4-char alphanumeric starting with a digit (e.g. 6XXX, 1CD9).
_PDB_RE = re.compile(r"^[0-9][A-Za-z0-9]{3}$")


@dataclass(frozen=True)
class Structure:
    """One 3D structure for a protein. `source` makes the honesty rule machine-readable."""

    source: str                     # "experimental" (RCSB/PDB) | "alphafold" (predicted)
    identifier: str                 # PDB id (e.g. "6XXX") or AlphaFold accession (the UniProt acc)
    method: str | None = None       # "X-RAY DIFFRACTION" | "ELECTRON MICROSCOPY" | "AlphaFold" ...
    resolution: float | None = None  # Å, experimental only; None for predicted models
    title: str | None = None
    # The authoritative coordinate-file URL + its format, RESOLVED from the source (RCSB probe /
    # AlphaFold API `pdbUrl`), never guessed. Guessing broke on two real cases: recent PDB entries
    # are mmCIF-only (no .pdb), and AlphaFold bumped its filenames v4->v6. The rule is the same one
    # the whole feature rests on — retrieve the identifier, don't author it. Defaulted so a cache
    # entry written before these fields still loads (viewer_url then falls back to the legacy guess).
    coord_url: str | None = None
    coord_format: str = "pdb"       # "pdb" | "mmcif" — 3Dmol.js needs to know which parser to use

    @property
    def viewer_url(self) -> str:
        """The coordinate file a viewer loads. Prefers the resolved `coord_url`; falls back to the
        legacy guessed path only for old cache entries that predate coord_url."""
        if self.coord_url:
            return self.coord_url
        if self.source == "experimental":
            return f"https://files.rcsb.org/download/{self.identifier}.pdb"
        return (f"https://alphafold.ebi.ac.uk/files/"
                f"AF-{self.identifier}-F1-model_v4.pdb")

    @property
    def page_url(self) -> str:
        if self.source == "experimental":
            return f"https://www.rcsb.org/structure/{self.identifier}"
        return f"https://alphafold.ebi.ac.uk/entry/{self.identifier}"


@dataclass(frozen=True)
class ProteinRecord:
    """The identity block for one gene's protein. Every field is a retrieved fact or null.

    `resolved` is False when UniProt returned nothing — the panel then declines to show an identity
    strip rather than guess an accession. A record can resolve identity but have no `structure`
    (structure lookup failed or none exists); those are independent, so a UniProt outage and a PDB
    outage degrade separately.
    """

    gene: str
    accession: str | None = None
    protein_name: str | None = None
    length: int | None = None
    structure: Structure | None = None
    resolved: bool = False

    def to_json(self) -> dict:
        return {
            "gene": self.gene,
            "accession": self.accession,
            "protein_name": self.protein_name,
            "length": self.length,
            "resolved": self.resolved,
            "structure": (None if self.structure is None else {
                "source": self.structure.source,
                "identifier": self.structure.identifier,
                "method": self.structure.method,
                "resolution": self.structure.resolution,
                "title": self.structure.title,
                "viewer_url": self.structure.viewer_url,
                "page_url": self.structure.page_url,
                "coord_format": self.structure.coord_format,
            }),
        }

    @classmethod
    def from_json(cls, d: dict) -> "ProteinRecord":
        s = d.get("structure")
        structure = None if not s else Structure(
            source=s.get("source", "experimental"), identifier=s.get("identifier", ""),
            method=s.get("method"), resolution=s.get("resolution"), title=s.get("title"),
            # viewer_url in the cache IS the resolved coord_url; restore it so a cached entry keeps
            # the authoritative URL rather than reverting to the guessed one.
            coord_url=s.get("viewer_url"), coord_format=s.get("coord_format", "pdb"))
        return cls(gene=d["gene"], accession=d.get("accession"),
                   protein_name=d.get("protein_name"), length=d.get("length"),
                   structure=structure, resolved=bool(d.get("resolved")))


# --- HTTP: fixed hosts, JSON in / JSON out, None on any failure -------------------------------


def _get_json(url: str, *, timeout: float = 15.0) -> dict | list | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — fixed hosts
            if r.status != 200:
                return None
            return json.loads(r.read())
    except Exception:  # noqa: BLE001 — degrade to None, never raise into the request path
        return None


def _post_json(url: str, payload: dict, *, timeout: float = 20.0) -> dict | None:
    try:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — fixed host
            if r.status != 200:
                return None
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


def _url_ok(url: str, *, timeout: float = 12.0) -> bool:
    """Does this coordinate URL actually serve a file? A HEAD probe, used to prefer .pdb but fall
    back to .cif — recent/large PDB entries are mmCIF-only and 404 on .pdb. Fixed hosts."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — fixed host
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


# --- UniProt: gene symbol -> reviewed human entry --------------------------------------------


def _resolve_uniprot(gene: str) -> ProteinRecord:
    """The reviewed (Swiss-Prot) human entry for a gene symbol. Reviewed-only so we get the
    canonical accession, not one of many unreviewed fragments; human-only via taxonomy 9606."""
    query = f'(gene_exact:{gene}) AND (organism_id:9606) AND (reviewed:true)'
    params = urllib.parse.urlencode({
        "query": query, "format": "json", "size": 1,
        "fields": "accession,protein_name,length,xref_pdb"})
    data = _get_json(f"{_UNIPROT_SEARCH}?{params}")
    results = (data or {}).get("results") if isinstance(data, dict) else None
    if not results:
        return ProteinRecord(gene=gene, resolved=False)

    entry = results[0]
    acc = (entry.get("primaryAccession") or "").strip().upper()
    if not _UNIPROT_RE.match(acc):
        return ProteinRecord(gene=gene, resolved=False)

    desc = (entry.get("proteinDescription") or {}).get("recommendedName") or {}
    name = ((desc.get("fullName") or {}).get("value")) or None
    length = (entry.get("sequence") or {}).get("length")
    return ProteinRecord(gene=gene, accession=acc, protein_name=name,
                         length=int(length) if length else None, resolved=True)


# --- RCSB: accession -> best experimental structure ------------------------------------------


def _best_experimental_structure(accession: str) -> Structure | None:
    """Lowest-resolution (best) experimental structure whose polymer maps to this UniProt acc.

    Uses the RCSB search API to find entries, sorted by resolution ascending, then reads the top
    entry's metadata for the method/resolution/title. Returns None if nothing maps — an honest
    'no experimental structure', which is the AlphaFold path's cue.
    """
    query = {
        "query": {
            "type": "terminal", "service": "text",
            "parameters": {
                "attribute":
                    "rcsb_polymer_entity_container_identifiers."
                    "reference_sequence_identifiers.database_accession",
                "operator": "exact_match", "value": accession},
        },
        "return_type": "entry",
        "request_options": {
            "sort": [{"sort_by": "rcsb_entry_info.resolution_combined",
                      "direction": "asc"}],
            "paginate": {"start": 0, "rows": 1}},
    }
    hits = _post_json(_RCSB_SEARCH, query)
    result_set = (hits or {}).get("result_set") if isinstance(hits, dict) else None
    if not result_set:
        return None
    pdb_id = (result_set[0].get("identifier") or "").strip().upper()
    if not _PDB_RE.match(pdb_id):
        return None

    meta = _get_json(f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}")
    method, resolution, title = None, None, None
    if isinstance(meta, dict):
        exptl = meta.get("exptl") or []
        if exptl:
            method = exptl[0].get("method")
        res = (meta.get("rcsb_entry_info") or {}).get("resolution_combined") or []
        if res:
            resolution = round(float(res[0]), 2)
        title = (meta.get("struct") or {}).get("title")

    # Resolve the real coordinate URL: prefer .pdb (widest viewer support), fall back to .cif for
    # the mmCIF-only entries that 404 on .pdb. Probed, not guessed — a guessed .pdb was the bug.
    pdb_url = f"https://files.rcsb.org/download/{pdb_id}.pdb"
    if _url_ok(pdb_url):
        coord_url, coord_format = pdb_url, "pdb"
    else:
        coord_url, coord_format = f"https://files.rcsb.org/download/{pdb_id}.cif", "mmcif"
    return Structure(source="experimental", identifier=pdb_id, method=method,
                     resolution=resolution, title=title,
                     coord_url=coord_url, coord_format=coord_format)


def _alphafold_structure(accession: str) -> Structure | None:
    """The AlphaFold predicted model for an accession, if the DB has one. Labelled `alphafold`
    and carrying no resolution — a prediction is never presented as a solved structure. The
    coordinate URL comes from the API's own `pdbUrl` field (versioned v4/v6/...), never a guessed
    filename — the version bump from v4 to v6 is exactly what broke the guessed URL."""
    data = _get_json(f"{_ALPHAFOLD_API}/{accession}")
    if not isinstance(data, list) or not data:
        return None
    entry = data[0]
    coord_url = entry.get("pdbUrl") or entry.get("cifUrl")
    coord_format = "pdb" if entry.get("pdbUrl") else "mmcif"
    return Structure(source="alphafold", identifier=accession, method="AlphaFold",
                     resolution=None,
                     title=f"AlphaFold predicted model ({accession})",
                     coord_url=coord_url, coord_format=coord_format)


# --- cache ------------------------------------------------------------------------------------


def _load_cache() -> dict:
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE))
        except (ValueError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    try:
        json.dump(cache, open(CACHE, "w"), indent=0)
    except OSError:
        pass


def resolve_protein(gene: str, *, use_cache: bool = True) -> ProteinRecord:
    """Resolve one gene to its protein identity + best available structure. Cached.

    Structure preference: an experimental PDB when one maps to the accession, else the AlphaFold
    model, else None. The record still carries identity even when structure is None, so a PDB/AF
    outage never suppresses the (independently retrieved) accession and length.
    """
    g = (gene or "").strip().upper()
    if not g:
        return ProteinRecord(gene=g, resolved=False)

    cache = _load_cache() if use_cache else {}
    if use_cache and g in cache:
        return ProteinRecord.from_json(cache[g])

    record = _resolve_uniprot(g)
    if record.resolved and record.accession:
        structure = (_best_experimental_structure(record.accession)
                     or _alphafold_structure(record.accession))
        record = replace(record, structure=structure)

    if use_cache:
        cache[g] = record.to_json()
        _save_cache(cache)
    return record


if __name__ == "__main__":
    # Smoke test: IL2RA has an approved antibody + solved structures; TSC1's canonical acc is Q92574
    # (the one the model got wrong). Both should resolve to real, on-pattern identifiers.
    for sym in ("IL2RA", "TSC1", "NFKB2"):
        rec = resolve_protein(sym, use_cache=False)
        st = rec.structure
        stx = "—" if not st else f"{st.source}:{st.identifier} ({st.resolution or 'n/a'} Å)"
        print(f"{sym:<7} acc={rec.accession} len={rec.length} struct={stx}  {rec.protein_name}")
