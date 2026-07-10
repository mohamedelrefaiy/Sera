"""Node A -- the ingestion / harmonization agent. UPSTREAM of the core, and gated by it.

What makes Concord more than two hardcoded loaders is that a screen it has never seen can be
mapped onto the canonical axes. Doing that by hand for every new file is what does not scale;
doing it by pattern-matching column names is what breaks the moment someone writes `lfc` instead
of `log2FoldChange`. So the mapping is PROPOSED by a model and DISPOSED of by code.

The contract, and the reason this is safe:

    the agent proposes a ColumnMapping -- a set of column NAMES and a regime label.
    it never sees, touches, transforms, or emits a single measured number.

An agent that hallucinates a p-value is a catastrophe. An agent that hallucinates a column name
raises KeyError, or is caught by `core.canonical.validate`, which independently re-derives
`is_ontarget`, re-establishes the sign convention from the data, and refuses anything that does
not hold together. Constraining the output TYPE constrains the blast radius.

Three things this module will not do:

  * silently drop a column. Unmapped columns are carried into the manifest, never guessed away.
  * silently pick a regime. When a file offers several, `choose_regime` applies the recorded
    policy, and the manifest names both what was chosen and what was discarded (Hazard 2).
  * silently proceed on a low-confidence mapping. It raises NeedsConfirmation for a human.

Degrades honestly: with no Anthropic credentials a deterministic heuristic proposer runs instead.
It emits the SAME ColumnMapping type through the SAME gate, and the manifest records which
proposer ran -- so a keyless clone builds and still tells the truth about how it got there. The
fallback is not a weaker guarantee; it is a weaker PROPOSER with identical guarantees, because
the guarantees live in the validator.
"""
from __future__ import annotations

import csv
import os
import shutil
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Sequence

from ..core.canonical import (
    SIGN_CONVENTION, CanonicalRow, Modality, SchemaViolation, SignifRegime, ValidationReport,
    apply_sign_correction, choose_regime, derive_ontarget, validate)

MODEL = "claude-haiku-4-5-20251001"   # column mapping is cheap, structured, high-volume

# How many example values per column the profiler shows the model. Enough to tell an FDR from a
# log-fold-change; few enough that a 19k-row screen costs a handful of tokens.
PROFILE_SAMPLE = 5

# Below this, the mapping is surfaced to a human rather than applied (brief 1.5).
CONFIRM_THRESHOLD = 0.75


# ---------------------------------------------------------------------------------------
# Closed vocabularies -- the agent CHOOSES an operation, it never SUPPLIES one
# ---------------------------------------------------------------------------------------
#
# The single most important safety property of this node: every transform the mapping can request
# is a named member of a closed enum, implemented by code here. The model picks `MIN_OF`; it never
# writes `min(a, b)`. There is no free-text formula field, so there is nothing to eval(), and every
# operation that ran is a token a reviewer can read in the manifest.


class SignifCombine(str, Enum):
    """How to get ONE significance value out of the mapped column(s).

    Why this exists: MAGeCK reports TWO one-sided FDRs per gene (`neg|fdr` = knockdown depletes the
    marker-high population; `pos|fdr` = it enriches). A gene is a hit if it moves the readout in
    EITHER direction, so the significance is min(neg|fdr, pos|fdr). Mapping a single column instead
    is not a rounding error -- measured on Schmidt CD4+ IL2 at q<0.10, taking `neg|fdr` alone finds
    73 hits where min() finds 663. It would silently discard 590 genes, 89% of the protein hits,
    every one of them a gene whose knockdown RAISES IL2 (a brake). The 2x2 would lose an entire
    quadrant and still look plausible.

    This is exactly the class of error a column-name-only mapping cannot express, and therefore the
    reason the vocabulary has to include the combining rule.
    """

    SINGLE = "single"      # one column, taken as-is (DESeq2 adj_p, an lfsr, ...)
    MIN_OF = "min_of"      # the most significant of several one-sided tests (MAGeCK)


class AxisExtract(str, Enum):
    """How to read an axis value out of a column whose cells are not already the bare value.

    Schmidt's readout axis is `phenotype` = "CD4+ IL2": cell type and cytokine fused into one
    string. Concord reconciles PER CYTOKINE, so comparing that string against Zhu's "IL2" finds no
    overlap at all. The column must be decomposed -- and the decomposition rule must be named in
    the manifest, not buried in a reader.
    """

    WHOLE = "whole"              # the cell is the value
    LAST_TOKEN = "last_token"    # "CD4+ IL2" -> "IL2"   (whitespace-split, take the last)
    FIRST_TOKEN = "first_token"  # "CD4+ IL2" -> "CD4+"  (then strip a trailing '+')


def _combine_signif(rec: dict, cols: Sequence[str], rule: SignifCombine, screen_id: str) -> float:
    """Apply the named combining rule. The ONLY place a significance value is computed."""
    try:
        vals = [float(rec[c]) for c in cols]
    except (KeyError, TypeError, ValueError) as e:
        raise SchemaViolation(f"[{screen_id}] cannot read significance column(s) {list(cols)}: {e}"
                              ) from e
    if rule is SignifCombine.SINGLE:
        if len(vals) != 1:
            raise SchemaViolation(f"[{screen_id}] combine='single' needs exactly one column, "
                                  f"got {len(vals)}: {list(cols)}")
        return vals[0]
    if rule is SignifCombine.MIN_OF:
        if len(vals) < 2:
            raise SchemaViolation(f"[{screen_id}] combine='min_of' needs >= 2 columns, "
                                  f"got {list(cols)}")
        return min(vals)
    raise SchemaViolation(f"[{screen_id}] unknown combine rule {rule!r}")


def _extract(value: str, rule: AxisExtract) -> str:
    """Apply the named extraction rule to one cell."""
    if rule is AxisExtract.WHOLE:
        return value
    parts = value.split()
    if not parts:
        return value
    if rule is AxisExtract.LAST_TOKEN:
        return parts[-1]
    if rule is AxisExtract.FIRST_TOKEN:
        return parts[0].rstrip("+")
    raise SchemaViolation(f"unknown extract rule {rule!r}")


# ---------------------------------------------------------------------------------------
# What the agent is shown, and what it is allowed to say
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnProfile:
    """One column, as the model sees it: names, types, and a few example values -- never the full
    column. The model cannot read the measurements in bulk, so it cannot form an opinion about the
    biology, only about which column is which. That is the point of profiling rather than handing
    over the file."""

    name: str
    dtype: str
    n_missing: int
    cardinality: int
    samples: tuple[str, ...]


@dataclass(frozen=True)
class ColumnMapping:
    """The agent's proposal: which column carries which canonical axis. Names only, no values.

    An axis may be a COLUMN (`cytokine_col`) or a stated CONSTANT (`cytokine_const`) when the file
    holds one value throughout -- Schmidt's whole CD4+ IL2 slice has no condition column, and the
    honest encoding of that is a named constant, not an invented column.

    `confidence` is the model's own estimate. Below CONFIRM_THRESHOLD it is surfaced to a human.
    """

    screen_id: str
    gene_col: str
    effect_col: str
    signif_cols: tuple[str, ...]              # one column, or the several a combine rule folds
    signif_combine: SignifCombine
    signif_regime: SignifRegime
    modality: Modality
    cytokine_col: str | None = None
    cytokine_const: str | None = None
    cytokine_extract: AxisExtract = AxisExtract.WHOLE
    condition_col: str | None = None
    condition_const: str | None = None
    cell_type_col: str | None = None
    cell_type_const: str | None = None
    cell_type_extract: AxisExtract = AxisExtract.WHOLE
    confidence: float = 1.0
    rationale: str = ""
    unmapped_columns: tuple[str, ...] = ()
    regimes_found: tuple[SignifRegime, ...] = ()      # Hazard 2: all of them, for the manifest
    regimes_discarded: tuple[SignifRegime, ...] = ()
    proposer: str = "heuristic"                       # "claude" | "heuristic"

    def axis(self, name: str) -> tuple[str | None, str | None]:
        """(column, constant) for one axis. Exactly one is expected to be set."""
        return getattr(self, f"{name}_col", None), getattr(self, f"{name}_const", None)


class NeedsConfirmation(Exception):
    """A mapping the agent was not confident about. Raised so a HUMAN resolves the ambiguity
    rather than the model resolving its own. Carries the proposal so a UI can render it."""

    def __init__(self, mapping: ColumnMapping, reason: str) -> None:
        super().__init__(f"[{mapping.screen_id}] mapping needs confirmation: {reason}")
        self.mapping = mapping
        self.reason = reason


# ---------------------------------------------------------------------------------------
# Step 1 -- profile (deterministic; the model reads this, not the file)
# ---------------------------------------------------------------------------------------


def profile_csv(path: str, sample: int = PROFILE_SAMPLE) -> tuple[ColumnProfile, ...]:
    """Profile every column: name, inferred dtype, missingness, cardinality, a few examples.

    `utf-8-sig` strips a UTF-8 BOM when present (Freimer has one on `id`, which would otherwise
    profile as a column literally named '\\ufeffid' and never match any hint).
    """
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = [c for c in (reader.fieldnames or []) if c is not None]
        seen: dict[str, list[str]] = {c: [] for c in cols}
        missing: dict[str, int] = {c: 0 for c in cols}
        uniq: dict[str, set[str]] = {c: set() for c in cols}
        for rec in reader:
            for c in cols:
                v = rec.get(c)
                if v is None or v == "":
                    missing[c] += 1
                    continue
                if len(seen[c]) < sample:
                    seen[c].append(v)
                if len(uniq[c]) < 1000:      # cap: cardinality only needs to distinguish a label
                    uniq[c].add(v)           # column from a measurement column, not count exactly

    return tuple(ColumnProfile(name=c, dtype=_infer_dtype(seen[c]), n_missing=missing[c],
                               cardinality=len(uniq[c]), samples=tuple(seen[c]))
                 for c in cols)


def _infer_dtype(samples: Sequence[str]) -> str:
    """float | int | str | empty, from the examples. Cheap and sufficient: it tells a measurement
    column from a label column, and is never used to parse anything."""
    if not samples:
        return "empty"
    try:
        vals = [float(s) for s in samples]
    except (TypeError, ValueError):
        return "str"
    return "int" if all(v == int(v) for v in vals) else "float"


# ---------------------------------------------------------------------------------------
# Step 2 -- propose (heuristic, or the model)
# ---------------------------------------------------------------------------------------

# Column-name evidence per canonical axis. Order matters: earlier is stronger evidence.
_GENE_HINTS = ("id", "gene", "symbol", "gene_name", "target", "target_gene")
# Effect must be a SIGNED effect size. `neg|lfc` is listed first because in MAGeCK output it is
# the log-fold-change (identical to `pos|lfc`), NOT a one-sided statistic -- a plain `lfc`
# substring search would otherwise be ambiguous across the two.
_EFFECT_HINTS = ("neg|lfc", "log2foldchange", "log2fc", "log_fc", "lfc", "zscore", "z_rna",
                 "effect", "z")
_REGIME_HINTS: tuple[tuple[str, SignifRegime], ...] = (
    ("adj_p_value", SignifRegime.DESEQ2_ADJP),
    ("padj", SignifRegime.DESEQ2_ADJP),
    ("adj_p", SignifRegime.DESEQ2_ADJP),
    ("lfsr", SignifRegime.MASH_LFSR),
    ("neg|fdr", SignifRegime.BENJAMINI_FDR),
    ("fdr", SignifRegime.BENJAMINI_FDR),
    ("q_value", SignifRegime.BENJAMINI_FDR),
    ("qvalue", SignifRegime.BENJAMINI_FDR),
)


def regimes_in(columns: Sequence[str]) -> tuple[SignifRegime, ...]:
    """Every significance regime the file appears to offer -- Hazard 2's input.

    A file carrying both `adj_p_value` and `lfsr` returns BOTH, so `choose_regime` can pick one by
    policy and the manifest can record what was discarded. Silence about a discarded regime is
    exactly how a mixed-regime table gets built without anyone noticing.
    """
    lowered = [c.lower() for c in columns]
    found: list[SignifRegime] = []
    for pattern, regime in _REGIME_HINTS:
        if any(pattern in c for c in lowered) and regime not in found:
            found.append(regime)
    return tuple(found)


def _first_match(columns: Sequence[str], hints: Sequence[str]) -> str | None:
    """Exact match first (so `id` beats `gene_id`), then substring. Returns None rather than a
    best guess -- a None becomes an unmapped axis, which the validator rejects loudly."""
    lowered = {c.lower(): c for c in columns}
    for h in hints:
        if h in lowered:
            return lowered[h]
    for h in hints:
        for c in columns:
            if h in c.lower():
                return c
    return None


def _signif_cols_for(columns: Sequence[str],
                     regime: SignifRegime) -> tuple[tuple[str, ...], SignifCombine]:
    """The column(s) carrying the chosen regime's value, and how to fold them into one number.

    Detects MAGeCK's two one-sided tests structurally: if BOTH a `neg|`- and a `pos|`-prefixed
    column exist for this regime, the significance is min() of the pair, because a gene is a hit
    when it moves the readout in either direction. Picking one of the pair would silently discard
    every gene significant only in the other direction -- 89% of Schmidt's protein hits.
    """
    patterns = [p for p, r in _REGIME_HINTS if r == regime]
    matches = [c for c in columns if any(p in c.lower() for p in patterns)]
    neg = [c for c in matches if c.lower().startswith("neg")]
    pos = [c for c in matches if c.lower().startswith("pos")]
    if neg and pos:
        return (neg[0], pos[0]), SignifCombine.MIN_OF
    one = _first_match(columns, patterns)
    return ((one,), SignifCombine.SINGLE) if one else ((), SignifCombine.SINGLE)


def propose_heuristic(profiles: Sequence[ColumnProfile], screen_id: str, *, modality: Modality,
                      cytokine_col: str | None = None, cytokine_const: str | None = None,
                      cytokine_extract: AxisExtract = AxisExtract.WHOLE,
                      condition_const: str | None = None,
                      cell_type_col: str | None = None, cell_type_const: str | None = None,
                      cell_type_extract: AxisExtract = AxisExtract.WHOLE) -> ColumnMapping:
    """The deterministic proposer: name-pattern matching, no model. Runs when there are no
    credentials, and serves as the reference the model's proposal is compared against in tests.

    Raises rather than guessing when an axis cannot be located. A screen whose gene column we
    cannot find is a screen we cannot read -- saying so is the correct outcome.
    """
    cols = [p.name for p in profiles]
    gene = _first_match(cols, _GENE_HINTS)
    effect = _first_match(cols, _EFFECT_HINTS)
    found = regimes_in(cols)
    regime = choose_regime(found) if found else None
    signif_cols, combine = _signif_cols_for(cols, regime) if regime else ((), SignifCombine.SINGLE)

    absent = [n for n, v in (("gene", gene), ("effect", effect),
                             ("significance", signif_cols or None)) if v is None]
    if absent:
        raise SchemaViolation(
            f"[{screen_id}] heuristic proposer could not locate: {', '.join(absent)}. "
            f"Columns present: {cols}. Refusing to guess an axis rather than map it.")

    mapped = {gene, effect, cytokine_col, cell_type_col, *signif_cols}
    return ColumnMapping(
        screen_id=screen_id, gene_col=gene, effect_col=effect,
        signif_cols=signif_cols, signif_combine=combine, signif_regime=regime, modality=modality,
        cytokine_col=cytokine_col, cytokine_const=cytokine_const, cytokine_extract=cytokine_extract,
        condition_const=condition_const,
        cell_type_col=cell_type_col, cell_type_const=cell_type_const,
        cell_type_extract=cell_type_extract,
        confidence=1.0, rationale="deterministic name-pattern match (no model)",
        unmapped_columns=tuple(c for c in cols if c not in mapped),
        regimes_found=found, regimes_discarded=tuple(r for r in found if r != regime),
        proposer="heuristic",
    )


def has_credentials() -> bool:
    """Same probe the explanation precompute uses. A keyless clone must still build."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.path.exists(os.path.expanduser("~/.claude/.credentials.json")):
        return True
    return shutil.which("claude") is not None


SYSTEM_PROMPT = (
    "You map a CRISPR-screen file's columns onto a fixed canonical schema. You are shown ONLY a "
    "column profile: names, dtypes, missingness, cardinality, and a few example values.\n"
    "Rules:\n"
    "- Output ONLY column NAMES and a regime label. Never output, transform, or comment on a "
    "measured value. You are choosing which column is which, nothing else.\n"
    "- Canonical axes: gene (the perturbed gene), cytokine (the readout), condition, cell_type, "
    "effect_size (signed), signif_value (plus its regime).\n"
    "- The effect column must be a SIGNED effect size (a log-fold-change or a z-score), never a "
    "score, a rank, or a p-value.\n"
    "- signif_regime is exactly one of: deseq2_adjp, benjamini_fdr, mash_lfsr, zscore, log2fc. "
    "If the file offers several, list every one in regimes_found; never blend them.\n"
    "- signif_cols + signif_combine: a MAGeCK screen reports TWO one-sided FDRs (a 'neg|' and a "
    "'pos|' column). A gene is a hit if it moves the readout in EITHER direction, so list BOTH "
    "columns and set signif_combine='min_of'. For a single significance column use 'single'. "
    "These are the only two rules; you may not describe any other arithmetic.\n"
    "- cytokine_extract / cell_type_extract: 'whole' if the cell already IS the value; "
    "'last_token' / 'first_token' if one column fuses two axes (e.g. a phenotype 'CD4+ IL2' has "
    "cell_type=first_token, cytokine=last_token). Only these three rules exist.\n"
    "- If a column cannot be confidently mapped, leave it out and list it in unmapped_columns. "
    "Never guess an axis -- lower your confidence instead.\n"
    "- confidence is your own honest [0,1] estimate that this mapping is correct."
)


def _profile_text(profiles: Sequence[ColumnProfile]) -> str:
    return "\n".join(
        f"- {p.name!r}: dtype={p.dtype}, missing={p.n_missing}, distinct~{p.cardinality}, "
        f"examples=[{', '.join(p.samples[:PROFILE_SAMPLE])}]"
        for p in profiles)


def user_prompt(profiles: Sequence[ColumnProfile], screen_id: str, modality: Modality) -> str:
    """The single-turn message: column profile in, JSON mapping out."""
    import json
    schema = {
        "gene_col": "str",
        "cytokine_col": "str|null", "cytokine_const": "str|null",
        "cytokine_extract": "whole|last_token|first_token",
        "condition_col": "str|null", "condition_const": "str|null",
        "cell_type_col": "str|null", "cell_type_const": "str|null",
        "cell_type_extract": "whole|last_token|first_token",
        "effect_col": "str",
        "signif_cols": ["..."], "signif_combine": "single|min_of",
        "signif_regime": "deseq2_adjp|benjamini_fdr|mash_lfsr|zscore|log2fc",
        "regimes_found": ["..."], "unmapped_columns": ["..."],
        "confidence": 0.0, "rationale": "one sentence",
    }
    return (f"Screen id: {screen_id}. Modality: {modality.value}.\n"
            f"Column profile:\n{_profile_text(profiles)}\n\n"
            "Return ONLY a JSON object with exactly these keys (no prose, no code fence):\n"
            + json.dumps(schema, indent=2))


def _parse_mapping(raw: str, screen_id: str, modality: Modality) -> ColumnMapping:
    """Parse the model's JSON into a typed proposal. Pure, so it is unit-testable without a key."""
    import json
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise SchemaViolation(f"[{screen_id}] Claude returned no JSON object: {text[:200]!r}")
    try:
        d: dict[str, Any] = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise SchemaViolation(f"[{screen_id}] unparseable mapping JSON: {e}") from e

    for required in ("gene_col", "effect_col", "signif_cols", "signif_regime"):
        if not d.get(required):
            raise SchemaViolation(f"[{screen_id}] mapping omits required key {required!r}")

    def _enum(cls, value, key: str):
        """Coerce into a closed vocabulary, or refuse. A value outside the enum means the model
        invented an operation -- exactly what the closed vocabulary exists to prevent, so it is a
        hard failure rather than a silent fallback to a default."""
        try:
            return cls(value)
        except ValueError as e:
            allowed = [m.value for m in cls]
            raise SchemaViolation(f"[{screen_id}] {key}={value!r} is not one of {allowed}; the "
                                  "mapping may only CHOOSE from the vocabulary, never extend it"
                                  ) from e

    regime = _enum(SignifRegime, d["signif_regime"], "signif_regime")
    combine = _enum(SignifCombine, d.get("signif_combine", "single"), "signif_combine")
    cyt_x = _enum(AxisExtract, d.get("cytokine_extract") or "whole", "cytokine_extract")
    cell_x = _enum(AxisExtract, d.get("cell_type_extract") or "whole", "cell_type_extract")

    valid = SignifRegime._value2member_map_
    found = tuple(SignifRegime(r) for r in d.get("regimes_found", ()) if r in valid)
    signif_cols = tuple(d["signif_cols"]) if isinstance(d["signif_cols"], list) \
        else (d["signif_cols"],)

    return ColumnMapping(
        screen_id=screen_id, gene_col=d["gene_col"], effect_col=d["effect_col"],
        signif_cols=signif_cols, signif_combine=combine, signif_regime=regime, modality=modality,
        cytokine_col=d.get("cytokine_col"), cytokine_const=d.get("cytokine_const"),
        cytokine_extract=cyt_x,
        condition_col=d.get("condition_col"), condition_const=d.get("condition_const"),
        cell_type_col=d.get("cell_type_col"), cell_type_const=d.get("cell_type_const"),
        cell_type_extract=cell_x,
        confidence=float(d.get("confidence", 0.0)), rationale=str(d.get("rationale", "")),
        unmapped_columns=tuple(d.get("unmapped_columns", ())),
        regimes_found=found or (regime,),
        regimes_discarded=tuple(r for r in found if r != regime),
        proposer="claude",
    )


async def propose_with_claude(profiles: Sequence[ColumnProfile], screen_id: str, *,
                              modality: Modality) -> ColumnMapping:
    """Ask Claude to propose the mapping. Single-turn, no tools -- it cannot read the file.

    Whatever it returns is still gated: the caller feeds the mapping to `ingest_csv`, whose rows go
    through `core.canonical.validate`. This function's only job is profile -> typed proposal.
    """
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock)

    options = ClaudeAgentOptions(system_prompt=SYSTEM_PROMPT, model=MODEL,
                                 max_turns=1, allowed_tools=[])
    chunks: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(user_prompt(profiles, screen_id, modality))
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
    return _parse_mapping("".join(chunks), screen_id, modality)


# ---------------------------------------------------------------------------------------
# Step 3 -- read the file THROUGH the mapping (deterministic; the agent is gone by now)
# ---------------------------------------------------------------------------------------


def _value(rec: dict, col: str | None, const: str | None, axis: str, screen_id: str,
           extract: AxisExtract = AxisExtract.WHOLE) -> str:
    """Resolve one axis from either its column (through the named extraction rule) or its stated
    constant. Refuses when neither is available -- an axis with no source is a broken mapping, and
    while the validator would reject the resulting empty string anyway, failing here names the axis
    that was left unmapped."""
    if col is not None:
        v = (rec.get(col) or "").strip()
        if not v:
            raise SchemaViolation(f"[{screen_id}] axis '{axis}': column {col!r} is empty in a row")
        return _extract(v, extract)
    if const:
        return const
    raise SchemaViolation(f"[{screen_id}] axis '{axis}' has neither a column nor a constant")


def rows_from_mapping(path: str, mapping: ColumnMapping, *,
                      drop_ids: frozenset[str] = frozenset(),
                      row_filter: Callable[[dict], bool] | None = None,
                      ) -> tuple[CanonicalRow, ...]:
    """Read a CSV through a validated mapping into canonical rows. No model, no inference.

    `row_filter` selects ONE readout from a multi-readout file: Schmidt's CSV holds both
    `CD4+ IL2` and `CD8+ IFNG`, Freimer's holds IL2/IL2RA/CTLA4. Ingesting them as one screen
    would mix cell types and cytokines under a single `screen_id` and a single sign calibration.
    Each readout is its own screen; the filter says which one this call is reading.

    `is_ontarget` is derived here from the data (gene == cytokine); the mapping cannot supply it.
    Signs are NOT corrected here -- `ingest_csv` does that as an explicit, logged step, so the
    correction appears in the manifest instead of happening invisibly inside a reader.
    """
    from .adapters import NON_GENE_IDS
    skip = NON_GENE_IDS | drop_ids
    sid = mapping.screen_id
    rows: list[CanonicalRow] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for rec in csv.DictReader(fh):
            gene = (rec.get(mapping.gene_col) or "").strip()
            if not gene or gene in skip:
                continue
            if row_filter and not row_filter(rec):
                continue
            try:
                effect = float(rec[mapping.effect_col])
            except (KeyError, TypeError, ValueError) as e:
                raise SchemaViolation(
                    f"[{sid}] row for {gene!r}: could not read effect via mapping "
                    f"(effect_col={mapping.effect_col!r}): {e}") from e
            signif = _combine_signif(rec, mapping.signif_cols, mapping.signif_combine, sid)
            rows.append(derive_ontarget(CanonicalRow(
                screen_id=sid, gene=gene,
                cytokine=_value(rec, mapping.cytokine_col, mapping.cytokine_const, "cytokine", sid,
                                mapping.cytokine_extract),
                condition=_value(rec, mapping.condition_col, mapping.condition_const,
                                 "condition", sid),
                cell_type=_value(rec, mapping.cell_type_col, mapping.cell_type_const,
                                 "cell_type", sid, mapping.cell_type_extract),
                modality=mapping.modality, effect_size=effect, signif_value=round(signif, 6),
                signif_regime=mapping.signif_regime,
            )))
    return tuple(rows)


@dataclass(frozen=True)
class IngestResult:
    """A completed, gated ingestion. `rows` are canonical, sign-corrected, and still carry their
    on-target rows -- the caller drops them (Hazard 1) after the manifest has recorded them.

    TWO validation reports, and the distinction is the whole point of an audit log:

      `source_report`  what the FILE said, before any correction. Its sign_check is the EVIDENCE
                       that justified a flip (e.g. "3 on-target rows all positive").
      `report`         what the rows say NOW, after correction. Its sign_check is the PROOF the
                       flip landed ("all negative; matches the convention").

    Logging only the second would make the manifest unfalsifiable: it would record that we flipped
    the signs, alongside evidence saying no flip was needed. A reviewer could check the outcome but
    never the reasoning. So both are kept, and `manifest()` reports the source's evidence.
    """

    rows: tuple[CanonicalRow, ...]
    mapping: ColumnMapping
    source_report: ValidationReport
    report: ValidationReport
    sign_corrected: bool


def ingest_csv(path: str, mapping: ColumnMapping, *,
               require_confirmation: bool = True,
               row_filter: Callable[[dict], bool] | None = None) -> IngestResult:
    """The gate, applied. Mapping in -> validated, sign-corrected canonical rows out.

    Order is load-bearing:

      1. read rows through the mapping        (on-target rows included -- they are the standard)
      2. validate                             (Hazards 1/2/3 + finiteness + regime + modality)
      3. correct the sign if inverted         (explicit, logged, reported in the result)
      4. re-validate after correction         (proves the correction actually landed)

    Step 4 is not paranoia. A correction that silently failed would leave every verdict for this
    screen flipped, and the whole point of Hazard 3 is that such a failure is invisible downstream.

    Raises NeedsConfirmation when the proposal's confidence is below CONFIRM_THRESHOLD -- the
    ambiguity is surfaced to a human, never resolved by the model that created it.
    """
    if require_confirmation and mapping.confidence < CONFIRM_THRESHOLD:
        raise NeedsConfirmation(
            mapping, f"confidence {mapping.confidence:.2f} < {CONFIRM_THRESHOLD}; "
                     f"proposed by {mapping.proposer}: {mapping.rationale}")

    rows = rows_from_mapping(path, mapping, row_filter=row_filter)
    source_report = validate(rows, mapping.screen_id, unmapped_columns=mapping.unmapped_columns)

    corrected = source_report.sign_check.inverted
    report = source_report
    if corrected:
        rows = apply_sign_correction(rows, source_report.sign_check)
        report = validate(rows, mapping.screen_id, unmapped_columns=mapping.unmapped_columns)
        if report.sign_check.inverted:                       # must not happen; check anyway
            raise SchemaViolation(
                f"[{mapping.screen_id}] sign correction did not take effect -- every verdict for "
                "this screen would be inverted. Refusing to proceed.")

    return IngestResult(rows=rows, mapping=mapping, source_report=source_report,
                        report=report, sign_corrected=corrected)


# ---------------------------------------------------------------------------------------
# Step 4 -- the manifest: how this screen was read, so a reviewer never has to take it on trust
# ---------------------------------------------------------------------------------------


def _sha256(path: str) -> str:
    """Hash the SOURCE file. The manifest is a receipt about an input, not a description of an
    output: 'given this exact file, here is every decision that was made'. Hashing the output would
    only prove the output is itself."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(result: IngestResult, source_path: str, *, run_label: str = "") -> dict[str, Any]:
    """The human-readable provenance record for one ingested screen (brief 1.3, 1.6).

    Every decision that could change a verdict appears here, named:
      * which column became which axis, and by which extraction rule
      * which significance columns were folded, and by which combining rule
      * which regime was CHOSEN and which were DISCARDED (Hazard 2 -- silence here is how a
        mixed-regime table gets built unnoticed)
      * whether the effect column was inverted and whether it was corrected (Hazard 3)
      * every column that went unmapped (never guessed away)
      * who proposed it -- the model, or the heuristic fallback

    Deliberately carries NO ambient timestamp. `run_label` is supplied by the caller. A manifest
    stamped with the current time is a different document on every run, which makes it impossible
    to diff two runs or hash-compare them in a test -- and reproducibility is the entire claim.
    """
    m, rep, src = result.mapping, result.report, result.source_report
    return {
        "run_label": run_label,
        "screen_id": m.screen_id,
        "source_file": os.path.basename(source_path),
        "source_sha256": _sha256(source_path),
        "proposer": m.proposer,
        "model": MODEL if m.proposer == "claude" else None,
        "confidence": round(m.confidence, 3),
        "rationale": m.rationale,
        "mapping": {
            "gene": m.gene_col,
            "cytokine": {"column": m.cytokine_col, "constant": m.cytokine_const,
                         "extract": m.cytokine_extract.value},
            "condition": {"column": m.condition_col, "constant": m.condition_const},
            "cell_type": {"column": m.cell_type_col, "constant": m.cell_type_const,
                          "extract": m.cell_type_extract.value},
            "effect_size": m.effect_col,
            "significance": {"columns": list(m.signif_cols), "combine": m.signif_combine.value,
                             "regime": m.signif_regime.value},
            "modality": m.modality.value,
        },
        "hazards": {
            "h1_ontarget_rows_found": src.n_ontarget,
            "h2_regime_chosen": rep.regime.value,
            "h2_regimes_found": [r.value for r in m.regimes_found],
            "h2_regimes_discarded": [r.value for r in m.regimes_discarded],
            # Hazard 3: the EVIDENCE (what the source file said) and the ACTION taken, kept apart.
            # A log that showed only post-correction counts would claim a flip was performed while
            # displaying evidence that no flip was needed -- unauditable. `source_*` justifies the
            # decision; `after_correction_*` proves it landed.
            "h3_sign_inverted_in_source": src.sign_check.inverted,
            "h3_sign_corrected": result.sign_corrected,
            "h3_source_evidence": src.sign_check.detail,
            "h3_source_ontarget_negative": src.sign_check.n_negative,
            "h3_source_ontarget_positive": src.sign_check.n_positive,
            "h3_after_correction": rep.sign_check.detail if result.sign_corrected else None,
        },
        "unmapped_columns": list(rep.unmapped_columns),
        "validation": {
            "n_rows": rep.n_rows,
            "regime": rep.regime.value,
            "modality": rep.modality.value,
            "sign_convention": SIGN_CONVENTION,
        },
    }


def write_manifest(manifests: Sequence[dict[str, Any]], path: str) -> str:
    """Write one or more screen manifests to `mapping_manifest.yaml`. Returns the path."""
    import yaml
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {"sign_convention": SIGN_CONVENTION, "screens": list(manifests)}
    with open(path, "w") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
    return path
