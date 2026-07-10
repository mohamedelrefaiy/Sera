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

A NOTE ON WHAT "CITED" HONESTLY MEANS HERE. A model asked for a PubMed ID will happily emit a
well-formed, plausible, WRONG one. Format-checking an accession proves only that it is
well-formed. So this module separates the two claims and never conflates them:

    check_format()    offline, always runs. A malformed citation is rejected outright.
    resolve()         network, opt-in. Confirms the accession actually exists.

`CitationStatus` records which check was performed. An entry whose citation was never resolved
says so, rather than borrowing the credibility of one that was. Selling a format check as
verification would be the same overclaim the doctrine forbids at the verdict layer.

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
    """What was actually checked. Never conflate a format check with an existence check."""

    FORMAT_OK = "format_ok"          # well-formed; existence NOT checked (offline)
    RESOLVED = "resolved"            # confirmed to exist against the live database
    UNRESOLVED = "unresolved"        # looked up and NOT found -- the claim must be dropped


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


# ---------------------------------------------------------------------------------------
# The enriched entry
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Hypothesis:
    """One candidate mechanism for a discordance, and the source that motivates it."""

    claim: str
    citation: Citation

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
    "disagree for one gene. You are given a JSON record of MEASURED values and a verdict that has "
    "ALREADY been decided by deterministic code.\n"
    "Hard rules:\n"
    "- You do NOT decide the verdict. It is given. Never contradict, revise, or re-rank it.\n"
    "- Use ONLY the numbers in the record. Never invent a p-value, q-value, effect size, or any "
    "figure not present. If a value is null, that side is untested -- say so, do not guess.\n"
    "- EVERY mechanistic claim must carry a citation: a PubMed ID (digits only) or a UniProt "
    "accession. A claim you cannot cite is a claim you must not make. Omit it instead.\n"
    "- Give 2-3 hypotheses, each one sentence, each cited.\n"
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


def user_prompt(record: dict[str, Any]) -> str:
    """Single-turn message. Verdict + numbers in; cited hypotheses + one experiment out."""
    schema = {
        "hypotheses": [{"claim": "one sentence",
                        "citation": {"db": "pubmed|uniprot", "accession": "str"}}],
        "distinguishing_experiment": "one specific assay, and which outcome favours which "
                                     "hypothesis",
    }
    return ("Propose cited mechanistic hypotheses for this ALREADY-DECIDED verdict, then one "
            "distinguishing experiment. Use ONLY these numbers:\n"
            + json.dumps(record, indent=2)
            + "\n\nReturn ONLY a JSON object of this shape (no prose, no code fence):\n"
            + json.dumps(schema, indent=2))


def parse_entry(raw: str, record: dict[str, Any]) -> ValidationEntry:
    """Parse the model's JSON into a validated entry. Pure -- unit-testable with no credentials.

    A hypothesis whose citation is malformed is DROPPED rather than kept uncited. If that leaves
    nothing, `validate_entry` refuses the whole entry: better an absent hypothesis than an
    unsourced one.
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
    for h in d.get("hypotheses", ()):
        cite = h.get("citation") or {}
        db_raw = str(cite.get("db", "")).lower()
        if db_raw not in CitationDB._value2member_map_:
            continue                                       # an un-citable claim is dropped
        candidate = Hypothesis(claim=str(h.get("claim", "")).strip(),
                               citation=Citation(db=CitationDB(db_raw),
                                                 accession=str(cite.get("accession", "")).strip()))
        try:
            kept.append(candidate.validate())
        except UncitedClaim:
            continue                                       # malformed accession -> drop the claim

    return validate_entry(ValidationEntry(
        gene=record["gene"], cytokine=record["cytokine"], condition=record["condition"],
        verdict=record["verdict"], hypotheses=tuple(kept),
        distinguishing_experiment=str(d.get("distinguishing_experiment", "")).strip(),
        grounded_from=record,
    ))


async def interpret(record: dict[str, Any]) -> ValidationEntry:
    """One enriched entry via a single-turn, no-tools Claude call. The verdict is an INPUT."""
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock)

    options = ClaudeAgentOptions(system_prompt=SYSTEM_PROMPT, model=MODEL,
                                 max_turns=1, allowed_tools=[])
    chunks: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(user_prompt(record))
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
    return parse_entry("".join(chunks), record)


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
