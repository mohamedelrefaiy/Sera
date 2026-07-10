"""Node B -- the interpretation / mechanism agent. DOWNSTREAM of the core, and unable to reach it.

Once the deterministic verdict exists, this node attaches what a bench scientist actually needs
next: why the two screens might disagree, and the one experiment that would tell the competing
explanations apart. It ENRICHES; it never decides.

The brief is blunt about the risk, and it is the right risk to name: this is the node most likely
to drift into "LLM narrates plausible biology" -- the exact failure Concord differentiates
against. Three constraints keep it tethered.

  1. IT CANNOT WRITE BACK. Nothing in `core/` or `pipeline/02_build_concordance.py` imports this
     module. There is no code path from a hypothesis to a verdict. That is architecture, not
     discipline -- and eval/test_writeback_invariant.py makes it executable by asserting the
     verdict table is hash-identical with this node on and off.

  2. EVERY CLAIM CARRIES A CITATION. A mechanistic sentence without a resolvable accession is
     rejected before it is ever written to disk. "No uncited claims. Ever."

  3. EVERY ENTRY ENDS IN A DISTINGUISHING EXPERIMENT. Not a summary -- a wet-lab test that
     discriminates hypothesis A from hypothesis B. Even the soft node stays falsifiable.

It also inherits the existing grounding contract (core/explanation.py): it may interpret only the
numbers it was handed, and may never invent an effect size or a p-value.

A NOTE ON WHAT "CITED" HONESTLY MEANS HERE -- and why this module RETRIEVES rather than recalls.

The first version of this node asked the model for a PubMed ID. It emitted well-formed, resolvable,
and completely irrelevant ones. Measured, across two independent samples, 8 of 8 citations existed
and 8 of 8 were unrelated to the claim they backed:

    VPS37B / ESCRT trafficking   -> PMID 28289289, "Fe(3+)-rich pyrolitic lower mantle" (geophysics)
    VPS37B / mRNA stabilization  -> PMID 29946018, plant etioplast membrane lipids
    ITK / IL-2 production        -> PMID 12421994, "acupuncture for chronic neck pain"
    TSC1 / mTORC1                -> PMID 25941405, cytoplasmic dynein
    TSC1 (UniProt)               -> Q16558, which is KCNMB1, a potassium channel (TSC1 is Q92574)

Every one passes a format check. Every one passes a resolvability check. The brief's stated gate --
"an accession that passes a format check (and, if online, resolves)" -- passes on all of them, and
the output is fabricated scholarship. Existence is not relevance, and a model does not know these
identifiers; no prompt makes it know them.

So the design changed. The model may no longer WRITE an accession. It is handed a numbered list of
papers actually retrieved from PubMed for this gene, and may only SELECT from it by index. A
citation is therefore unforgeable by construction: `Citation` can only be built from a `Paper` that
came back from a real search. The same containment principle as Node A's closed operation
vocabulary -- delete the dangerous free-text field rather than police it.

Three statuses, never conflated, because they are three different claims:

    FORMAT_OK   well-formed. Says nothing about whether it exists.
    RESOLVED    exists in the live database. Says nothing about whether it is on-topic.
    RETRIEVED   came back from a PubMed query for THIS gene, and the model chose it from that set.
                This is the only status that carries topical relevance, and the only one Node B
                emits. It is still not proof the paper supports the claim -- a human reads it. The
                label says "HYPOTHESIS", and the URL is one click away.

This module never opens concordance.parquet -- not to read, and certainly not to write.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

MODEL = "claude-haiku-4-5-20251001"   # short, grounded, structured; no tools

# The UI label. Verbatim from the brief -- it must be impossible to mistake a hypothesis for a
# finding, so the string travels with the data rather than living only in a template.
HYPOTHESIS_LABEL = "HYPOTHESIS — not used in verdict"

# Node B only speaks about verdicts where mRNA and protein genuinely disagree. A `replicated` gene
# needs no mechanistic excuse, and a `neither` gene has nothing to explain.
INTERPRETABLE_VERDICTS = ("protein_only", "mrna_only", "discordant")


# ---------------------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------------------


class CitationDB(str, Enum):
    """The two identifier spaces the brief names. A closed set: an agent cannot cite 'a review'."""

    PUBMED = "pubmed"
    UNIPROT = "uniprot"


# PubMed IDs are 1-8 digits with no leading zero. UniProt accessions follow the official pattern
# (https://www.uniprot.org/help/accession_numbers): 6 or 10 characters in a fixed shape. Both are
# deliberately strict -- a loose pattern would wave a hallucinated string through the only offline
# check we have.
_PMID_RE = re.compile(r"^[1-9]\d{0,7}$")
_UNIPROT_RE = re.compile(
    r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})$")


class CitationStatus(str, Enum):
    """What was actually established. Three different claims; never conflate them.

    The ordering is one of increasing evidence, and only the last carries topical relevance:
    a well-formed PMID may not exist; an existing PMID may be about the lower mantle.
    """

    FORMAT_OK = "format_ok"          # well-formed; existence NOT checked (offline)
    RESOLVED = "resolved"            # confirmed to exist -- but says nothing about the topic
    RETRIEVED = "retrieved"          # came back from a PubMed query for THIS gene, and was
    #                                  selected from that set. The only status Node B emits.
    UNRESOLVED = "unresolved"        # looked up and NOT found -- the claim must be dropped


@dataclass(frozen=True)
class Paper:
    """A real paper, returned by a real search. The only thing a Citation may be built from."""

    pmid: str
    title: str

    def as_citation(self) -> "Citation":
        return Citation(CitationDB.PUBMED, self.pmid, CitationStatus.RETRIEVED)


# NCBI throttles unauthenticated E-utilities to ~3 requests/second and returns HTTP 429 above it
# (observed while building this). A short sleep between calls is the documented remedy.
_NCBI_DELAY_S = 0.4
_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def search_pubmed(gene: str, mechanism_terms: Sequence[str], *, retmax: int = 6,
                  timeout: float = 10.0) -> tuple[Paper, ...]:
    """Retrieve real papers for one gene, scoped to a mechanism. The model never sees a PMID it
    did not get from here.

    Returns () on any network failure. That is not a silent swallow: a caller with no papers has
    nothing to cite, and `interpret` then refuses to emit an uncited entry rather than falling back
    on the model's memory -- which is exactly the failure this function exists to prevent.
    """
    import time
    import urllib.error
    import urllib.parse
    import urllib.request

    terms = " OR ".join(f'"{t}"' for t in mechanism_terms) if mechanism_terms else ""
    query = f'{gene}[Title/Abstract]' + (f" AND ({terms})" if terms else "")

    def _get(path: str, params: dict) -> dict | None:
        url = f"{_EUTILS}/{path}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — fixed host
                if resp.status != 200:
                    return None
                return json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return None

    found = _get("esearch.fcgi", {"db": "pubmed", "retmode": "json", "retmax": retmax,
                                  "sort": "relevance", "term": query})
    ids = (found or {}).get("esearchresult", {}).get("idlist", []) if found else []
    if not ids:
        return ()

    time.sleep(_NCBI_DELAY_S)
    summary = _get("esummary.fcgi", {"db": "pubmed", "retmode": "json", "id": ",".join(ids)})
    result = (summary or {}).get("result", {})
    papers = tuple(Paper(pmid=i, title=result[i].get("title", "").strip())
                   for i in ids if i in result and result[i].get("title"))
    return papers


@dataclass(frozen=True)
class Citation:
    """A resolvable identifier backing exactly one mechanistic claim."""

    db: CitationDB
    accession: str
    status: CitationStatus = CitationStatus.FORMAT_OK

    def check_format(self) -> bool:
        """Offline validity. True says 'this could be a real accession', never 'this is one'."""
        acc = self.accession.strip()
        if self.db is CitationDB.PUBMED:
            return bool(_PMID_RE.match(acc))
        return bool(_UNIPROT_RE.match(acc.upper()))

    @property
    def url(self) -> str:
        if self.db is CitationDB.PUBMED:
            return f"https://pubmed.ncbi.nlm.nih.gov/{self.accession}/"
        return f"https://www.uniprot.org/uniprotkb/{self.accession}"


class UncitedClaim(ValueError):
    """A mechanistic claim arrived without a well-formed citation. It never reaches disk."""


# Bracketed reference markers the model sometimes leaves in the prose ("... initiation [2, 3].")
# when it wanted to cite several papers but the schema allows one index. They point at nothing --
# the real citation travels in its own field -- so a reader would follow a phantom reference.
_REF_MARKER = re.compile(r"\s*\[\s*\d+(?:\s*[,;]\s*\d+)*\s*\]")


def _strip_ref_markers(claim: str) -> str:
    """Remove dangling `[2, 3]`-style markers. The citation is attached structurally, not inline."""
    return _REF_MARKER.sub("", claim).strip()


# ---------------------------------------------------------------------------------------
# The enriched entry
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Hypothesis:
    """One candidate mechanism for a discordance, and the retrieved source that motivates it."""

    claim: str
    citation: Citation
    source_title: str = ""          # the retrieved paper's title, so a reviewer sees WHAT was cited

    def validate(self) -> "Hypothesis":
        """Reject at construction time, not at render time. An uncited claim must not survive as a
        value that some later code path could accidentally display."""
        if not self.claim.strip():
            raise UncitedClaim("empty mechanistic claim")
        if not self.citation.check_format():
            raise UncitedClaim(
                f"claim {self.claim[:60]!r} carries a malformed {self.citation.db.value} "
                f"accession {self.citation.accession!r}; no uncited claims, ever")
        return self


@dataclass(frozen=True)
class ValidationEntry:
    """The enriched plan for one verdict: labelled hypotheses + one distinguishing experiment.

    `grounded_from` is the exact numeric record the model was handed, stored beside the prose so
    the existing grounding gate (eval/test_explanations.py) can assert that every number in the
    text traces back to a measurement. Provenance is stored, not asserted.
    """

    gene: str
    cytokine: str
    condition: str
    verdict: str
    hypotheses: tuple[Hypothesis, ...]
    distinguishing_experiment: str
    grounded_from: dict[str, Any]
    label: str = HYPOTHESIS_LABEL

    def to_json(self) -> dict[str, Any]:
        return {
            "gene": self.gene, "cytokine": self.cytokine, "condition": self.condition,
            "verdict": self.verdict,
            "label": self.label,
            "hypotheses": [{"claim": h.claim,
                            "citation": {"db": h.citation.db.value,
                                         "accession": h.citation.accession,
                                         "status": h.citation.status.value,
                                         "title": h.source_title,
                                         "url": h.citation.url}}
                           for h in self.hypotheses],
            "distinguishing_experiment": self.distinguishing_experiment,
            "grounded_from": self.grounded_from,
        }


def validate_entry(entry: ValidationEntry) -> ValidationEntry:
    """Enforce the brief's three hard rules on one entry before it can be written.

    Each check names the failure it prevents:
      * at least one hypothesis      -- an empty entry is noise dressed as analysis
      * every claim cited            -- an uncited mechanism IS the narration failure mode
      * a distinguishing experiment  -- without one, the entry is a story, not a hypothesis
      * the label is present         -- a hypothesis that can be mistaken for a finding is worse
                                        than no hypothesis at all
    """
    if not entry.hypotheses:
        raise UncitedClaim(f"[{entry.gene}] no hypotheses survived citation checking; an empty "
                           "entry must not be written")
    for h in entry.hypotheses:
        h.validate()
    if not entry.distinguishing_experiment.strip():
        raise UncitedClaim(
            f"[{entry.gene}] no distinguishing experiment; a mechanism nobody can test apart from "
            "its rival is a story, not a hypothesis")
    if entry.label != HYPOTHESIS_LABEL:
        raise UncitedClaim(f"[{entry.gene}] entry is not labelled {HYPOTHESIS_LABEL!r}")
    return entry


# ---------------------------------------------------------------------------------------
# The prompt -- inherits the grounding contract, adds the citation contract
# ---------------------------------------------------------------------------------------


SYSTEM_PROMPT = (
    "You propose MECHANISTIC HYPOTHESES for why a CRISPR screen's mRNA and protein readouts "
    "disagree for one gene. You are given a JSON record of MEASURED values, a verdict that has "
    "ALREADY been decided by deterministic code, and a numbered list of REAL papers retrieved from "
    "PubMed for this gene.\n"
    "Hard rules:\n"
    "- You do NOT decide the verdict. It is given. Never contradict, revise, or re-rank it.\n"
    "- Use ONLY the numbers in the record. Never invent a p-value, q-value, effect size, or any "
    "figure not present. If a value is null, that side is untested -- say so, do not guess.\n"
    "- NEVER write a PubMed ID or a UniProt accession. You cite by the INDEX of a paper in the "
    "provided list, and by nothing else. If no listed paper supports a mechanism you were going to "
    "propose, DROP that mechanism. A claim you cannot ground in the list is a claim you must not "
    "make.\n"
    "- Only cite a paper whose TITLE is plausibly about the mechanism you are claiming. Do not "
    "stretch. Returning ONE well-grounded hypothesis is better than three loosely-grounded ones.\n"
    "- Each hypothesis must propose a DISTINCT mechanism and cite a DISTINCT paper. Do not restate "
    "one mechanism twice, and do not cite the same paper for two hypotheses -- if only one paper "
    "supports one mechanism, return exactly one hypothesis. The whole point is that the "
    "distinguishing experiment can tell your hypotheses apart, which is impossible if they are the "
    "same idea or rest on the same evidence.\n"
    "- Give 1-3 hypotheses, each one sentence, each citing a DIFFERENT paper index.\n"
    "- Then give exactly ONE distinguishing experiment: a specific wet-lab test whose outcome "
    "would tell your hypotheses APART. Not a summary, not 'validate in vivo' -- name the assay "
    "and say which result favours which hypothesis.\n"
    "- For protein_only (protein moves, mRNA does not), consider: post-transcriptional or "
    "translational regulation; protein stability and turnover; surface trafficking (FACS reads "
    "SURFACE protein); epitope masking.\n"
    "- For mrna_only (mRNA moves, protein does not), consider: buffering; translational "
    "repression; compensating turnover; marker decoupling.\n"
    "- For discordant (both move, opposite directions), consider a regulator acting at two levels "
    "with opposite sign.\n"
    "- These are hypotheses, not findings. Do not claim discovery or novelty."
)


def build_record(row: dict[str, Any]) -> dict[str, Any]:
    """The exact numeric record the model may interpret -- the verdict, and the numbers behind it.

    Reuses the shape and direction labels of core/explanation.py so ONE grounding gate can police
    both nodes. Nothing is added that the deterministic core did not compute.
    """
    from ..core.explanation import _direction_label
    return {
        "gene": row["gene"], "cytokine": row["cytokine"], "condition": row["condition"],
        "verdict": row["verdict"],
        "mrna_perturbseq": {"z_score": row["z_rna"], "adj_p_value": row["q_rna"],
                            "is_hit": row["hit_rna"],
                            "direction": _direction_label(row["rna_promotes"])},
        "protein_facs": {"log_fold_change": row["lfc_prot"], "fdr": row["q_prot"],
                         "is_hit": row["hit_prot"],
                         "direction": _direction_label(row["prot_promotes"])},
    }


def user_prompt(record: dict[str, Any], papers: Sequence[Paper]) -> str:
    """Single-turn message: verdict + numbers + a numbered list of REAL papers.

    The model cites `paper_index`. There is no field in which it could write an accession, so a
    hallucinated PMID has nowhere to go -- the same containment as Node A's closed vocabulary.
    """
    schema = {
        "hypotheses": [{"claim": "one sentence",
                        "paper_index": 0}],
        "distinguishing_experiment": "one specific assay, and which outcome favours which "
                                     "hypothesis",
    }
    listing = "\n".join(f"  [{i}] {p.title}" for i, p in enumerate(papers))
    return ("Propose mechanistic hypotheses for this ALREADY-DECIDED verdict, each grounded in one "
            "of the retrieved papers below, then one distinguishing experiment.\n\n"
            "Measured values (use ONLY these numbers):\n"
            + json.dumps(record, indent=2)
            + f"\n\nRetrieved papers for {record['gene']} (cite by index; you may not write a "
              f"PMID):\n{listing}\n\n"
            "If no paper supports a mechanism, omit that hypothesis rather than stretching a "
            "citation.\n\nReturn ONLY a JSON object of this shape (no prose, no code fence):\n"
            + json.dumps(schema, indent=2))


def parse_entry(raw: str, record: dict[str, Any],
                papers: Sequence[Paper]) -> ValidationEntry:
    """Parse the model's JSON into a validated entry. Pure -- unit-testable with no credentials.

    A citation is an INDEX into `papers`, resolved here against the real, retrieved set. An index
    that is out of range, missing, or not an integer drops its claim. If that leaves nothing,
    `validate_entry` refuses the whole entry: better an absent hypothesis than an unsourced one.

    An `accession` or `pmid` key in the model's output is a protocol violation -- it means the model
    tried to write an identifier it was told it could not write -- and is a hard failure, not a
    dropped claim. Silently ignoring it would let the next prompt regression go unnoticed.
    """
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise UncitedClaim(f"[{record['gene']}] model returned no JSON object")
    try:
        d = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise UncitedClaim(f"[{record['gene']}] unparseable hypothesis JSON: {e}") from e

    kept: list[Hypothesis] = []
    used_papers: set[int] = set()
    for h in d.get("hypotheses", ()):
        for forbidden in ("accession", "pmid", "citation"):
            if forbidden in h:
                raise UncitedClaim(
                    f"[{record['gene']}] the model wrote {forbidden!r} directly. It may only cite "
                    "by paper_index, into the retrieved set -- an identifier it authors is exactly "
                    "the hallucinated-citation failure this design removes.")
        idx = h.get("paper_index")
        if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(papers)):
            continue                                   # un-groundable claim -> dropped
        if idx in used_papers:
            # One paper backs at most one hypothesis. Two claims resting on the same source are not
            # two hypotheses -- a distinguishing experiment cannot separate what the same evidence
            # supports (observed live: two TSC1 claims both cited the one TSC2/mTOR paper). Keeping
            # the first makes the citation the KEY: a hypothesis is defined by the evidence that
            # motivates it, so identical evidence was never a second hypothesis.
            continue
        candidate = Hypothesis(claim=_strip_ref_markers(str(h.get("claim", ""))),
                               citation=papers[idx].as_citation(),
                               source_title=papers[idx].title)
        try:
            validated = candidate.validate()
        except UncitedClaim:
            continue
        kept.append(validated)
        used_papers.add(idx)

    return validate_entry(ValidationEntry(
        gene=record["gene"], cytokine=record["cytokine"], condition=record["condition"],
        verdict=record["verdict"], hypotheses=tuple(kept),
        distinguishing_experiment=str(d.get("distinguishing_experiment", "")).strip(),
        grounded_from=record,
    ))


# Mechanism vocabulary per verdict class, used to scope the PubMed query. These are the mechanisms
# the brief names in §2.2 -- the search is aimed at the biology the verdict actually implies, rather
# than at whatever the model felt like claiming.
MECHANISM_TERMS: dict[str, tuple[str, ...]] = {
    "protein_only": ("post-transcriptional regulation", "protein stability", "protein turnover",
                     "trafficking", "translation"),
    "mrna_only": ("translational repression", "mRNA stability", "protein turnover",
                  "post-transcriptional regulation"),
    "discordant": ("post-transcriptional regulation", "protein stability", "mTOR", "translation"),
}


async def interpret(record: dict[str, Any], *,
                    papers: Sequence[Paper] | None = None) -> ValidationEntry:
    """One enriched entry via a single-turn, no-tools Claude call. The verdict is an INPUT.

    Papers are RETRIEVED first (or supplied, for tests) and handed to the model, which may only
    cite them by index. If retrieval returns nothing, we refuse rather than let the model fall back
    on its memory -- a recalled PMID is how this node produced a geophysics paper to support a claim
    about ESCRT trafficking.
    """
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock)

    verdict = record["verdict"]
    if verdict not in INTERPRETABLE_VERDICTS:
        # A `replicated` gene has no disagreement to explain and a `neither` gene has no signal.
        # Offering a mechanism for either is decoration -- and decoration attached to a verdict is
        # how a hypothesis starts being read as a finding.
        raise UncitedClaim(
            f"[{record['gene']}] verdict {verdict!r} is not interpretable. Node B speaks only "
            f"about {INTERPRETABLE_VERDICTS}, where the two screens actually disagree.")

    if papers is None:
        papers = search_pubmed(record["gene"], MECHANISM_TERMS.get(verdict, ()))
    if not papers:
        raise UncitedClaim(
            f"[{record['gene']}] no papers retrieved; refusing to interpret. A hypothesis with no "
            "retrieved source would have to be cited from memory, which is the failure this node "
            "is designed to make impossible.")

    options = ClaudeAgentOptions(system_prompt=SYSTEM_PROMPT, model=MODEL,
                                 max_turns=1, allowed_tools=[])
    chunks: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(user_prompt(record, papers))
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
    return parse_entry("".join(chunks), record, papers)


# ---------------------------------------------------------------------------------------
# Optional, opt-in: does the citation actually EXIST?
# ---------------------------------------------------------------------------------------


def resolve(citation: Citation, *, timeout: float = 5.0) -> Citation:
    """Confirm an accession exists, against the live database. Network; opt-in; never on the hot
    path.

    Returns a new Citation carrying RESOLVED or UNRESOLVED. This is the only check that
    distinguishes a real identifier from a well-formed hallucination -- which is exactly why it is
    reported as its own status rather than folded into `check_format`. A caller that cannot reach
    the network keeps FORMAT_OK, and must not present that as verification.
    """
    import urllib.error
    import urllib.request

    if not citation.check_format():
        return Citation(citation.db, citation.accession, CitationStatus.UNRESOLVED)

    if citation.db is CitationDB.PUBMED:
        url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json"
               f"&id={citation.accession}")
    else:
        url = f"https://rest.uniprot.org/uniprotkb/{citation.accession}.json"

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — fixed hosts
            if resp.status != 200:
                return Citation(citation.db, citation.accession, CitationStatus.UNRESOLVED)
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return citation            # network unavailable: stay FORMAT_OK, never claim RESOLVED

    if citation.db is CitationDB.PUBMED:
        # eutils returns HTTP 200 with an error stub for an unknown id; a real record has a title.
        try:
            rec = json.loads(body).get("result", {}).get(citation.accession, {})
            found = bool(rec.get("title")) and "error" not in rec
        except (ValueError, AttributeError):
            found = False
    else:
        found = len(body) > 0

    return Citation(citation.db, citation.accession,
                    CitationStatus.RESOLVED if found else CitationStatus.UNRESOLVED)


def write_plan(entries: Sequence[ValidationEntry], path: str) -> str:
    """Write the enriched validation plan. Keyed gene|cytokine|condition, like the explanation
    cache, so the two artifacts line up for a reviewer.

    This file is an ATTACHMENT to the verdict table, never an edit of it.
    """
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {f"{e.gene}|{e.cytokine}|{e.condition}": e.to_json() for e in entries}
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=1)
    return path
