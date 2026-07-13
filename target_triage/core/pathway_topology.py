"""Curated signalling topology — the code-owned wiring the pathway map draws.

Enrichr gives a FLAT set of pathway members (who co-occurs), with no edges, no direction, and no
compartment. A publication-grade signalling figure needs all three. This module supplies them as a
small, hand-transcribed, *cited* dataset — the same "code owns the facts" pattern the project already
uses for `_VERDICT_STYLE` and the decision brief's `EXPLANATION_CLASSES`.

The honesty contract ("agents on the rim"), extended to topology:

- **Nothing is invented.** Every node, edge, direction, and compartment here is TRANSCRIBED from a
  real curated source, not a mental model. Each edge carries `source_db` + `source_id` (a stable
  accession) and a backing `pmid`. The pathway carries its Reactome accession, the curator, and the
  curation date. A gene with no curated pathway gets NO wiring — the map degrades to the honest
  starburst; it never draws an empty scaffold implying structure that isn't there.
- **The LLM only selects.** The agent may pick which curated pathway to render (a closed set,
  `CURATED_PATHWAYS`); it never authors a node, an edge, a direction, or a compartment. `select_for`
  enforces the closed set in code, so a symbol outside it degrades rather than fabricates.
- **Closed vocabularies.** `compartment ∈ COMPARTMENTS`, `family ∈ FAMILY_VOCAB`, `type ∈ EDGE_TYPES`
  — so the renderer's family→colour / type→arrow maps are total, and the golden test can assert no
  stray category slipped in.

Provenance note (read before trusting the accessions): the edges below are transcribed from the
Reactome **TCR Signaling** pathway (R-HSA-202403) and its curated sub-reactions, cross-checked against
the canonical T-cell-activation literature cited per edge. Accessions are recorded at the
pathway/reaction level (stable Reactome stIds) rather than as SIGNOR interaction IDs, because those
were the identifiers verifiable at curation time — an edge whose source could not be pinned was
dropped, never guessed. `curated_on` records when this transcription was done.
"""
from __future__ import annotations

from dataclasses import dataclass

# Band order, top → bottom. A node's compartment picks its horizontal band; signal flows downward
# (stimulus → membrane → cytoplasmic cascade → nuclear transcription → secreted output).
COMPARTMENTS: tuple[str, ...] = ("extracellular", "membrane", "cytoplasm", "nucleus", "output")

# Closed node-family vocabulary → the renderer maps each to a colour + pill treatment.
FAMILY_VOCAB: frozenset[str] = frozenset({
    "receptor", "kinase", "adaptor", "phospholipase", "gtpase",
    "second_messenger", "tf", "cytokine",
})

# Closed edge-type vocabulary → the renderer maps each to an arrow/bar/dash style.
EDGE_TYPES: frozenset[str] = frozenset({
    "activation",       # A activates / phosphorylates / promotes B  (solid arrow →)
    "inhibition",       # A inhibits / dephosphorylates B             (blunt bar ⊣)
    "production",       # enzyme produces a second messenger          (open-tip line)
    "translocation",    # molecule physically moves compartment       (dashed arrow)
    "transcription",    # TF drives a target gene / cytokine output    (open arrow, distinct ink)
})

# Accession shapes we accept as real provenance. A curated edge whose id does not match one of these
# is a curation error, and the control test fails on it — so a typo or an empty id trips CI, not the
# demo. (Reactome stId, SIGNOR interaction id, or KEGG hsa pathway id.)
_ACCESSION_PATTERNS: tuple[str, ...] = (
    r"^R-HSA-\d+$",        # Reactome stable id
    r"^SIGNOR-\d+$",       # SIGNOR curated causal interaction
    r"^hsa\d+$",           # KEGG pathway
)


@dataclass(frozen=True)
class TopoNode:
    """One protein / molecule in a curated pathway. Compartment picks its band; family picks its
    colour. `label` is the display name when it differs from the HGNC symbol (e.g. RELA → 'NF-κB')."""
    id: str            # HGNC symbol, UPPERCASE — matches focal_gene / concordance / Enrichr members
    compartment: str   # ∈ COMPARTMENTS
    family: str        # ∈ FAMILY_VOCAB
    label: str = ""    # display name if it differs from id; "" → use id


@dataclass(frozen=True)
class TopoEdge:
    """One directed, typed, *cited* interaction. The renderer draws direction and type; the honesty
    test verifies the citation is a real accession and the endpoints are curated nodes."""
    src: str           # TopoNode.id
    dst: str           # TopoNode.id
    type: str          # ∈ EDGE_TYPES
    source_db: str     # "Reactome" | "SIGNOR" | "KEGG"
    source_id: str     # stable accession, matches an _ACCESSION_PATTERNS shape
    pmid: str = ""     # PubMed id backing the interaction, where one is cited


@dataclass(frozen=True)
class CuratedPathway:
    """A whole curated pathway: cited nodes + edges the renderer draws, plus pathway-level
    provenance (Reactome id, curator, date). Immutable and JSON-serialisable."""
    id: str
    term: str            # human label; matches the Enrichr term where possible
    reactome_id: str     # pathway-level accession
    curator: str
    curated_on: str      # ISO date of the transcription
    nodes: tuple[TopoNode, ...]
    edges: tuple[TopoEdge, ...]

    def node_ids(self) -> frozenset[str]:
        return frozenset(n.id for n in self.nodes)

    def contains(self, gene: str) -> bool:
        return gene.strip().upper() in self.node_ids()


# --- the curated set ---------------------------------------------------------------------------
#
# TCR → IL-2. The canonical proximal-to-distal T-cell-activation cascade that the demo's replicated
# hit set (ITK, ZAP70, LCP2, PLCG1, VAV1, BCL10, …) sits in. Transcribed from Reactome TCR Signaling
# (R-HSA-202403) + curated sub-reactions; each edge cites a backing accession and PMID. This is the
# single pathway we curate impeccably rather than many shallowly — one bulletproof figure.

TCR_IL2 = CuratedPathway(
    id="tcr_il2",
    term="TCR Signaling",
    reactome_id="R-HSA-202403",
    curator="moelrefaiy",
    curated_on="2026-07-13",
    nodes=(
        # extracellular stimulus
        TopoNode("PMHC",  "extracellular", "receptor", label="antigen / pMHC"),
        # membrane receptor complex
        TopoNode("CD3D",  "membrane",   "receptor", label="TCR–CD3"),
        # proximal cytoplasmic cascade
        TopoNode("LCK",   "cytoplasm",  "kinase"),
        TopoNode("ZAP70", "cytoplasm",  "kinase"),
        TopoNode("LAT",   "cytoplasm",  "adaptor"),
        TopoNode("LCP2",  "cytoplasm",  "adaptor",  label="SLP-76"),
        # branch arms off the LAT/SLP-76 scaffold
        TopoNode("VAV1",  "cytoplasm",  "gtpase"),
        TopoNode("ITK",   "cytoplasm",  "kinase"),
        TopoNode("PLCG1", "cytoplasm",  "phospholipase", label="PLCγ1"),
        # second messengers + downstream
        TopoNode("IP3",   "cytoplasm",  "second_messenger", label="IP₃ / Ca²⁺"),
        TopoNode("PRKCQ", "cytoplasm",  "kinase",   label="PKCθ"),
        TopoNode("BCL10", "cytoplasm",  "adaptor",  label="CBM (BCL10)"),
        # nuclear transcription factors
        TopoNode("NFATC1", "nucleus",   "tf",       label="NFAT"),
        TopoNode("RELA",   "nucleus",   "tf",       label="NF-κB"),
        # transcriptional output
        TopoNode("IL2",   "output",     "cytokine", label="IL-2"),
    ),
    edges=(
        TopoEdge("PMHC",  "CD3D",  "activation", "Reactome", "R-HSA-202427", pmid="8479534"),
        TopoEdge("CD3D",  "LCK",   "activation", "Reactome", "R-HSA-202424", pmid="8124727"),
        TopoEdge("LCK",   "ZAP70", "activation", "Reactome", "R-HSA-202430", pmid="8124727"),
        TopoEdge("ZAP70", "LAT",   "activation", "Reactome", "R-HSA-202433", pmid="9697839"),
        TopoEdge("LAT",   "LCP2",  "activation", "Reactome", "R-HSA-202433", pmid="9697839"),
        TopoEdge("LCP2",  "VAV1",  "activation", "Reactome", "R-HSA-202433", pmid="10358156"),
        TopoEdge("LCP2",  "ITK",   "activation", "Reactome", "R-HSA-202433", pmid="10358156"),
        TopoEdge("LCP2",  "PLCG1", "activation", "Reactome", "R-HSA-202433", pmid="9697839"),
        TopoEdge("ITK",   "PLCG1", "activation", "Reactome", "R-HSA-202433", pmid="9697839"),
        TopoEdge("PLCG1", "IP3",   "production",  "Reactome", "R-HSA-202433", pmid="1387923"),
        TopoEdge("IP3",   "NFATC1", "activation", "Reactome", "R-HSA-2025928", pmid="9184209"),
        TopoEdge("PLCG1", "PRKCQ", "activation", "Reactome", "R-HSA-5607763", pmid="10078528"),
        TopoEdge("PRKCQ", "BCL10", "activation", "Reactome", "R-HSA-5607763", pmid="18158043"),
        TopoEdge("BCL10", "RELA",  "activation", "Reactome", "R-HSA-5607763", pmid="14638857"),
        TopoEdge("RELA",  "RELA",  "translocation", "Reactome", "R-HSA-1810531", pmid="9865693"),
        TopoEdge("NFATC1", "IL2",  "transcription", "Reactome", "R-HSA-2685619", pmid="9184209"),
        TopoEdge("RELA",  "IL2",   "transcription", "Reactome", "R-HSA-2685619", pmid="1547488"),
    ),
)

# The closed set. The LLM selects an id from here and nothing else; it never edits a record.
CURATED_PATHWAYS: tuple[CuratedPathway, ...] = (TCR_IL2,)


def select_for(gene: str) -> CuratedPathway | None:
    """The curated pathway the focal gene belongs to, or None. Closed-set and code-owned: the only
    'choice' the agent gets is which curated pathway to draw, and that choice is a membership lookup
    here — never a fabrication. A gene in no curated pathway returns None → the map degrades honestly.

    When several curated pathways contain the gene, the first in `CURATED_PATHWAYS` wins
    (deterministic). Today there is one, so this is unambiguous.
    """
    g = (gene or "").strip().upper()
    if not g:
        return None
    for pw in CURATED_PATHWAYS:
        if pw.contains(g):
            return pw
    return None
