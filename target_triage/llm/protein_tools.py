"""The protein mini-report tool: identity + structure + cited literature, all authored by CODE.

`protein_report` answers "what do we actually know about this protein" as a single card. It is the
composition of three code-owned retrievals, and the agent's only job is to decide to call it and
narrate one framing line around the returned facts:

    clients.protein.resolve_protein  -> UniProt accession, protein name, length, best structure id
    llm.literature.summarise_literature -> 2-5 factual bullets, each cited by paper INDEX
    (pathway context is already the pathway_map tool -- this card links to it, not re-draws it)

Every fact on the card is retrieved from an authoritative source; none is authored by the model.
The accession is the exact identifier class rim/interpret.py caught the model fabricating, so it
comes from UniProt or not at all. The literature bullets are unforgeable by construction (see
llm/literature.py). The card degrades SECTION BY SECTION -- a PubMed outage blanks only the
literature block, a UniProt outage only the identity strip -- so no single source failure blanks
the whole report.

Same closed-set gene gate and same `_view` / `__view_update__` protocol as concord_tools.py; this
tool is registered into the same in-process MCP server.
"""
from __future__ import annotations

from claude_agent_sdk import tool

from ..clients.protein import resolve_protein
from .concord_tools import _GENES, _view
from .literature import summarise_literature


@tool(
    "protein_report",
    "Assemble a PROTEIN MINI-REPORT for a gene: its protein identity (UniProt accession, name, "
    "length), the best available 3D structure (an experimental PDB entry if one exists, else the "
    "AlphaFold predicted model), and a short CITED summary of what is known about the protein from "
    "the literature. Use when the user asks what we know about a protein/gene, for its structure, "
    "its UniProt/PDB entry, its sequence length, a background/overview, or a 'mini-report' / "
    "'dossier' on the protein itself (distinct from gene_evidence, which is druggability + disease "
    "scores). Every fact is retrieved from UniProt / RCSB / PubMed; narrate around them, never "
    "invent an accession, a PDB id, or a citation.",
    {"gene": str},
)
async def protein_report(args):
    gene = (args.get("gene") or "").strip().upper()
    if gene not in _GENES:
        return _view({"gene": gene, "error": "not in the screens"},
                     {"action": "protein_report", "gene": gene, "error": "not in the screens"})

    # Identity + structure: fast, cached, code-owned. Never raises (client degrades to null fields).
    record = resolve_protein(gene)

    # Literature: live LLM + PubMed. Isolated so a retrieval/timeout failure blanks ONLY this block;
    # the card still shows identity + structure. Same honesty as enrichment=None in gene_evidence.
    try:
        panel = await summarise_literature(gene)
        literature = panel.to_json()
    except Exception:  # noqa: BLE001 — a lit-retrieval failure must not sink the whole report
        literature = {"gene": gene, "resolved": False, "findings": []}

    identity = record.to_json()
    n_findings = len(literature.get("findings", []))
    has_structure = identity.get("structure") is not None

    # WORDS the agent narrates from — no accession/PDB id/PMID in here, those live on the card so the
    # agent cannot mis-transcribe one. The note steers it to frame, not to read the identifiers out.
    plain = {
        "resolved": bool(identity.get("resolved")),
        "protein_name": identity.get("protein_name"),
        "has_structure": has_structure,
        "structure_kind": (None if not has_structure
                           else identity["structure"]["source"]),   # experimental | alphafold
        "n_findings": n_findings,
    }

    return _view(
        {"gene": gene, "identity": identity, "literature": literature, "plain": plain,
         "note": ("Narrate ONE framing sentence for a bench scientist — e.g. name the protein and "
                  "say the card below carries its structure and cited literature. Do NOT read out "
                  "the UniProt accession, the PDB id, or any PMID; the card shows those, and they "
                  "are the identifiers you must never author. If resolved is false, say the "
                  "identity could not be retrieved rather than guessing one.")},
        {"action": "protein_report", "gene": gene,
         "identity": identity, "literature": literature},
    )
