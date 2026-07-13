"""The "what do we know about this protein" panel -- a cited literature summary, by INDEX only.

This is the descriptive sibling of rim/interpret.py. Node B asks "why might these two screens
DISAGREE for this gene" and demands a distinguishing experiment; this module asks the plainer
question a mini-report needs -- "what is the established biology of this protein" -- and wants
breadth, not a wet-lab test. Different question, different prompt, different output shape.

What it does NOT change is the one thing that must never change: the model may not author a
citation. It reuses rim's unforgeable primitives wholesale --

    search_pubmed(gene, terms) -> tuple[Paper]     real papers, retrieved for THIS gene
    Paper.as_citation()        -> Citation         the only thing a Citation may be built from
    the cite-by-index parse discipline             an `accession`/`pmid` key is a hard failure

-- so a "summary bullet" is exactly as forgeable as a Node B hypothesis: not at all. Each bullet
is one factual sentence about the protein, grounded in one retrieved paper, cited by that paper's
index into the retrieved list. A bullet the model cannot ground in the list is dropped; if none
survive, the panel is empty and the card says so, rather than narrating from the model's memory --
the same containment that stopped Node B citing a geophysics paper for ESCRT trafficking.

Placement note: this lives under llm/ (with concord_tools.py and the other SDK callers), NOT under
core/. The write-back invariant (eval/test_writeback_invariant.py) forbids core/ from importing an
agent; this module IS an agent call, so it stays out of core/ by construction.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Sequence

from ..rim.interpret import (
    Citation, CitationDB, CitationStatus, Paper, UncitedClaim, _strip_ref_markers, search_pubmed)

MODEL = "claude-haiku-4-5-20251001"   # short, grounded, structured; no tools -- same as Node B

# Precomputed panels for the demo genes live here (written by pipeline/07_warm_protein_reports.py).
# The live tool prefers this cache so a demo turn serves an instant, deterministic panel rather than
# a fresh ~3s PubMed+LLM round-trip. A cache MISS falls through to the live call — the cache is a
# demo accelerator, never a correctness dependency, so a keyless or un-warmed clone still works.
_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", "artifacts", "literature_cache.json")

# Descriptive scope, not mechanistic. Node B scopes PubMed to disagreement-mechanism terms
# ("protein turnover", "translational repression"); a "what we know" panel wants the protein's
# core biology, so the query is the gene against broad functional facets. Kept small and general
# on purpose -- relevance sorting does the rest, and a narrow query would starve the closed set the
# model must choose from.
FACET_TERMS: tuple[str, ...] = ("function", "signaling", "regulation", "immune", "T cell")

# A retrieved list of this size gives the model a real choice while staying inside NCBI's polite
# rate. Node B uses 6; a descriptive panel benefits from a couple more to summarise across.
RETMAX = 8


@dataclass(frozen=True)
class Finding:
    """One factual statement about the protein, and the retrieved paper that backs it."""

    statement: str
    citation: Citation
    source_title: str = ""

    def validate(self) -> "Finding":
        """Reject an uncited or empty finding at construction, exactly as Hypothesis.validate does --
        an unsourced 'fact' must never survive as a value some render path could display."""
        if not self.statement.strip():
            raise UncitedClaim("empty finding statement")
        if not self.citation.check_format():
            raise UncitedClaim(
                f"finding {self.statement[:60]!r} carries a malformed citation "
                f"{self.citation.accession!r}; no uncited findings, ever")
        return self


@dataclass(frozen=True)
class LiteraturePanel:
    """The cited 'what we know' block for one gene. `resolved` is False when retrieval returned
    nothing -- the card then shows an honest 'no literature retrieved', never a summary from memory."""

    gene: str
    findings: tuple[Finding, ...]
    resolved: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "gene": self.gene,
            "resolved": self.resolved,
            "findings": [{"statement": f.statement,
                          "citation": {"db": f.citation.db.value,
                                       "accession": f.citation.accession,
                                       "status": f.citation.status.value,
                                       "title": f.source_title,
                                       "url": f.citation.url}}
                         for f in self.findings],
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "LiteraturePanel":
        """Rebuild a panel from its cached JSON. The rebuilt Findings carry the SAME retrieved
        citations — a cached panel is exactly as grounded as a freshly-generated one, because the
        accession was unforgeable when it was written and JSON round-tripping cannot forge one."""
        finds = []
        for f in d.get("findings", ()):
            c = f.get("citation", {})
            db = CitationDB(c.get("db", "pubmed"))
            status = CitationStatus(c.get("status", CitationStatus.RETRIEVED.value))
            finds.append(Finding(statement=f.get("statement", ""),
                                 citation=Citation(db, c.get("accession", ""), status),
                                 source_title=c.get("title", "")))
        return cls(gene=d["gene"], findings=tuple(finds), resolved=bool(d.get("resolved", True)))


SYSTEM_PROMPT = (
    "You summarise what is ESTABLISHED about a human protein, for a bench scientist's mini-report. "
    "You are given the gene symbol and a numbered list of REAL papers retrieved from PubMed for it. "
    "Hard rules:\n"
    "- Each finding is ONE factual sentence about the protein's biology (its function, its role in "
    "a pathway, its regulation, its disease links). Plain, declarative, no hedging filler.\n"
    "- NEVER write a PubMed ID or any accession. You cite a finding by the INDEX of the paper in "
    "the provided list, and by nothing else. If no listed paper supports a statement you were going "
    "to make, DROP it. A statement you cannot ground in the list is one you must not make.\n"
    "- Only cite a paper whose TITLE is plausibly about the statement. Do not stretch a citation to "
    "cover a claim the title does not support. One well-grounded finding beats three loose ones.\n"
    "- Each finding cites a DISTINCT paper index. Do not cite the same paper for two findings.\n"
    "- Give 2-5 findings. Order them from the protein's core/established function to its more "
    "specific roles. These are summaries of published work, not your own conclusions or discoveries."
)


def user_prompt(gene: str, papers: Sequence[Paper]) -> str:
    """Single-turn message: the gene + a numbered list of REAL papers. The model cites by index;
    there is no field in which it could write an accession, so a hallucinated PMID has nowhere
    to go -- the same containment as Node B's user_prompt."""
    schema = {"findings": [{"statement": "one factual sentence", "paper_index": 0}]}
    listing = "\n".join(f"  [{i}] {p.title}" for i, p in enumerate(papers))
    return (f"Summarise what is established about the human protein {gene}, grounding each finding "
            "in one of the retrieved papers below.\n\n"
            f"Retrieved papers for {gene} (cite by index; you may not write a PMID):\n{listing}\n\n"
            "If no paper supports a statement, omit it rather than stretching a citation.\n\n"
            "Return ONLY a JSON object of this shape (no prose, no code fence):\n"
            + json.dumps(schema, indent=2))


def parse_panel(raw: str, gene: str, papers: Sequence[Paper]) -> LiteraturePanel:
    """Parse the model's JSON into a validated panel. Pure -- unit-testable with no credentials.

    A citation is an INDEX into `papers`, resolved here against the real retrieved set. An index
    that is out of range, missing, or not an integer drops its finding; a paper already used drops
    the duplicate. An `accession`/`pmid`/`citation` key is a PROTOCOL VIOLATION (the model wrote an
    identifier it was told it could not) and is a hard failure, not a dropped finding -- identical
    to rim/interpret.parse_entry, so the same regression cannot pass here silently.
    """
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise UncitedClaim(f"[{gene}] model returned no JSON object")
    try:
        d = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise UncitedClaim(f"[{gene}] unparseable literature JSON: {e}") from e

    kept: list[Finding] = []
    used_papers: set[int] = set()
    for f in d.get("findings", ()):
        for forbidden in ("accession", "pmid", "citation"):
            if forbidden in f:
                raise UncitedClaim(
                    f"[{gene}] the model wrote {forbidden!r} directly. It may only cite by "
                    "paper_index, into the retrieved set -- an authored identifier is exactly the "
                    "hallucinated-citation failure this design removes.")
        idx = f.get("paper_index")
        if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(papers)):
            continue                                     # un-groundable finding -> dropped
        if idx in used_papers:
            continue                                     # one paper backs at most one finding
        candidate = Finding(statement=_strip_ref_markers(str(f.get("statement", ""))),
                            citation=papers[idx].as_citation(),
                            source_title=papers[idx].title)
        try:
            kept.append(candidate.validate())
        except UncitedClaim:
            continue
        used_papers.add(idx)

    return LiteraturePanel(gene=gene, findings=tuple(kept), resolved=True)


# --- demo cache: read on the hot path, written by the warm-cache pipeline ----------------------


def _load_cache() -> dict:
    if os.path.exists(_CACHE):
        try:
            with open(_CACHE) as fh:
                return json.load(fh)
        except (ValueError, OSError):
            return {}
    return {}


def cached_panel(gene: str) -> LiteraturePanel | None:
    """The precomputed panel for a gene, or None on a miss. Only a RESOLVED cached panel is served
    — a cached empty/unresolved entry (retrieval failed when it was warmed) falls through to a live
    retry rather than showing a stale 'no literature' to the demo."""
    entry = _load_cache().get((gene or "").strip().upper())
    if not entry:
        return None
    panel = LiteraturePanel.from_json(entry)
    return panel if (panel.resolved and panel.findings) else None


def write_cache(panels: Sequence["LiteraturePanel"]) -> str:
    """Persist panels to the demo cache, keyed by gene. Merges with any existing cache so warming a
    few extra genes never drops the ones already warmed. Used by pipeline/07_warm_protein_reports."""
    cache = _load_cache()
    for p in panels:
        cache[p.gene.strip().upper()] = p.to_json()
    os.makedirs(os.path.dirname(_CACHE), exist_ok=True)
    with open(_CACHE, "w") as fh:
        json.dump(cache, fh, indent=1)
    return _CACHE


async def summarise_literature(gene: str, *, papers: Sequence[Paper] | None = None,
                               use_cache: bool = True) -> LiteraturePanel:
    """One cited 'what we know' panel. Serves the precomputed demo cache when present, else a
    single-turn, no-tools Claude call.

    Papers are RETRIEVED first (or supplied, for tests) and handed to the model, which may only
    cite them by index. If retrieval returns nothing, the panel is empty-and-honest rather than a
    summary from memory -- a recalled PMID is how the interpretation node produced a geophysics
    paper to support a claim about ESCRT trafficking. `use_cache=False` forces a live call (the
    warm-cache pipeline uses it so it regenerates rather than reading its own stale output).
    """
    g = (gene or "").strip().upper()
    if use_cache and papers is None:
        hit = cached_panel(g)
        if hit is not None:
            return hit

    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock)

    if papers is None:
        papers = search_pubmed(g, FACET_TERMS, retmax=RETMAX)
    if not papers:
        return LiteraturePanel(gene=g, findings=(), resolved=False)

    options = ClaudeAgentOptions(system_prompt=SYSTEM_PROMPT, model=MODEL,
                                 max_turns=1, allowed_tools=[])

    async def _one_call() -> str:
        chunks: list[str] = []
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_prompt(g, papers))
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            chunks.append(block.text)
        return "".join(chunks)

    # The SDK occasionally returns an EMPTY assistant turn (observed live on NFKB2/IL2RA/VAV1 while
    # warming) — a transient no-op, not a real refusal. One bounded retry recovers it; without the
    # retry an empty response raised "no JSON object" and dropped the whole panel. We do NOT loosen
    # the parse to tolerate junk — empty is empty, the fix is to ask again, not to accept less.
    raw = await _one_call()
    if not raw.strip():
        raw = await _one_call()
    if not raw.strip():
        return LiteraturePanel(gene=g, findings=(), resolved=False)
    return parse_panel(raw, g, papers)
