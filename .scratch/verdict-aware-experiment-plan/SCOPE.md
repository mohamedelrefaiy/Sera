# Scope — Verdict-aware experimental plan (Fable-reviewed)

**Status:** ship-blocker (#1) DONE + verified. #2–#3 scoped below, not yet built.
**Owner:** Mo (solo). **Deadline context:** hackathon Mon 2026-07-13 21:00 ET — scope discipline applies.
**Source of the critique:** Fable advisor review, 2026-07-14 (see conversation). Confirmed the user's instinct.

---

## What was wrong (the finding)

The bench-plan card renders **scientifically false copy for `neither`-verdict genes**. NRAS @ IL2/Stim48hr
is a *concordant null* — both screens measured it, both saw nothing (q≈0.98) — but the card called it a
"one-sided result" and told the user to "test the missing evidence layer." That inverts the data: nothing
is missing; the two screens *agree* the gene does nothing. Root cause: a JS `else` fall-through
(`renderResolution`) let `neither` inherit the template written for the one-sided (`mrna_only`/`protein_only`)
case. The same card elsewhere correctly said "DEPRIORITISE," so it contradicted itself.

Fable's deeper point (the "missing lots of details" the user sensed): **one generic 5-line protocol cannot be
the decisive experiment for all five verdicts**, and a concordant null usually warrants *no* experiment at all.

---

## #1 — Ship-blocker (DONE, verified in live DOM)

**File:** `sera/frontend/sera-app.html`, `renderResolution()` (~line 2740).
Replaced the single `else` with four explicit branches:

| Data case | Branch | Framing |
|---|---|---|
| `neither` + both tested | **concordant-null** | "Both screens agree GENE does nothing here." Deprioritise by default. Tiles INVERTED: "something changes" = headline (overturns null); added "positive control fails -> inconclusive" branch. |
| `prot_tested===false` (any verdict) | **coverage-gap** | "Measure the untested protein layer." This really *is* a missing layer. |
| `mrna_only`/`protein_only` + both tested | **one-sided** | "Test whether the effect reaches the other layer" (the original, now correctly scoped). |
| `discordant`, `replicated` | unchanged | — |

**Verified (browser, real data):** NRAS->concordant-null copy renders; TSC1 (discordant) untouched;
branch selector correct for NRAS / TSC1 / ABHD18 (neither+untested) / ATP5F1A (mrna_only+untested) / ABCA3
(mrna_only+both). Data check: 1,614 `neither` rows have `prot_tested=False` — the coverage-gap split is real,
not hypothetical.

---

## #1 residual (rolls into #3, NOT patched in #1)

The `js-benchSpec` table in the *sibling* function `renderDecisionBrief` (~line 2733) still prints the generic
protocol ("same condition / NTC only / measure both") for every verdict — so on a `neither` card my new copy
now says "add a positive control, vary the timing" while the spec table below still says "same condition, NTC
only." Self-contradiction #2. Fixing it *is* the #3 redesign, so it was deliberately left for that phase.

---

## #2 — Positive control + assay-sensitivity framing (biggest cross-verdict gap)

Fable: the single most important omission across ALL verdicts. Without a positive control that proves the
assay *can* detect an IL-2 effect, "neither changed" is uninterpretable (dead assay vs. true null). Currently
NTC is the *only* control on the card.

**Change:** the bench spec must carry, for every verdict:
- a **positive control** row (perturb a known IL-2 regulator in the same wells) — candidate must be
  code-selected from the screen's own strong hits, never LLM-invented (respect the "agents on the rim"
  contract).
- an **assay-sensitivity / minimum-detectable-effect** line so a null claim is only as strong as its power.
- keep NTC as the negative control (already present).

**Open question for build:** where does the positive-control gene come from? Must be a code-owned selection
(e.g. top replicated hit at the same condition), not LLM-authored. Confirm the artifact has a clean way to
pick one before building.

---

## #3 — Verdict-specific experiment templates (the real redesign)

Fable: the generic protocol fits ~2 of 5 verdicts and is *actively wrong* for two.

| Verdict | Decisive question | What the protocol must be | vs. current generic |
|---|---|---|---|
| **replicated** | reconciliation already settled | escalate toward mechanism/relevance: dose-response, on-target rescue, magnitude on secreted IL-2 | generic re-measure is LOW-VALUE |
| **discordant** | *why* do the layers disagree? | **TIME COURSE** (transcript/protein decouple transiently) + artifact checks | generic single-timepoint CANNOT resolve — WRONG |
| **mrna_only** | does transcript reach protein? | propagation test + protein-assay sensitivity floor | generic is OK-ish (closest fit) |
| **protein_only** | post-transcriptional, or Perturb-seq miss? | transcript at multiple timepoints vs. transcript sensitivity floor | OK-ish |
| **neither** (both tested) | is the null true, or a shared blind spot? | null-INTERROGATION: positive control + **vary** the shared axis (timing/dose/cell-state), or NO experiment | generic "same condition" does the OPPOSITE — WRONG |
| **neither / any (prot untested)** | coverage gap | measure the untested layer | genuine missing-layer case |

Cross-cutting protocol details Fable flagged as "not bench-ready" despite the "bench-ready outline" tag
(prioritised): (1) **modality ambiguous** — card says both "knock out" and "CRISPRi/perturbation"; KO != knockdown,
pick one and state it; (2) **no perturbation-efficiency validation** (TIDE/qPCR/Western) — any verdict
uninterpretable without it; (3) positive control (see #2); (4) **power structure** — n>=3 with no MDE, and
primary CD4+ IL-2 is highly donor-variable -> paired/mixed-model, not just n>=3; (5) **readout & timing** —
secreted vs intracellular IL-2; transcript peaks early while a lone 48h readout can miss it; (6) stimulation
dose/paradigm must match the source screens.

---

## Key architectural decision to make BEFORE building #3

There are **two parallel plan systems** in the repo and they must not diverge further:

- **A. Frontend template card** — `renderDecisionBrief` + `renderResolution` in `sera-app.html`. Verdict copy
  is hardcoded in JS. **This is the card in the screenshot** (reached via `?gene=` / reconcile view).
- **B. Backend decision brief** — `sera/core/decision_brief.py` (`_experiment_and_outcomes`, the
  `_OUTCOMES_*` matrices, `timecourse` field), rendered by `appendBriefTurn`. Already partially
  verdict-aware (last session added `_OUTCOMES_NO_SIGNAL`, readout-reflects-constraints). **This is the
  "plan" intent card.**

**DECISION MADE (converge on B):** the reconcile card (A) now RENDERS the backend brief (B) instead of
re-deriving science in JS. `loadGene` already fetched `decision_brief` and discarded it; now it stores it on
the turn. `renderResolution` + the `js-benchSpec` table render `brief.experiment` (objective, verdict-specific
protocol, positive control, assay sensitivity) + `brief.outcome_matrix`, with a row-based fallback only when
no brief exists. The old hardcoded verdict copy is gone. Result: one controls-gated source of truth; A inherits
B's 67-test golden control for free. DONE + verified in the live DOM across all 5 archetypes.

**Bug found + fixed while wiring (per-condition brief):** the API returned only the ANCHOR-condition brief, so
switching condition tabs showed the wrong verdict's experiment for genes whose verdict differs by condition
(e.g. ATP1B3 = replicated @ Stim48hr but protein_only @ Rest). Fixed in the BACKEND (user's call): extracted
`_brief_for_row(row, constraints)`, kept `_decision_brief_for` as the anchor entry point, and attached a
per-condition brief to every `by_condition` entry. Frontend reads `row.decision_brief` (current condition)
before `turn._brief` (anchor fallback). Verified: tab-switch now swaps the whole card consistently.

---

## Suggested build order (controls-first)

1. **Decide A-vs-B convergence** (above). ~30 min, needs your call — it's a product/architecture decision.
2. **#2 in `decision_brief.py`**: add positive-control + sensitivity to the `Experiment` dataclass + builder;
   extend golden test (`test_decision_brief.py`) to pin a code-selected positive control per verdict. TDD.
3. **#3 in `decision_brief.py`**: verdict-specific experiment shape (time-course for discordant, null-
   interrogation for neither, escalate for replicated). Extend the control to assert each verdict's protocol
   differs correctly. TDD.
4. **Frontend**: make the card(s) render B's fields; remove A's hardcoded verdict copy (or retire A).
5. **Verify** each in the browser against a representative gene per verdict (NRAS/TSC1/ABCA3/ABHD18/a
   replicated gene), plus `pytest sera/eval`.

**Routing when we build:** #1 done (inline). #2/#3 science + control = Sonnet under TDD (well-specified now);
consult Fable again only if the positive-control selection or the discordant time-course design opens a new
judgment call. Frontend wiring = Sonnet/inline. Mechanical copy moves = Haiku.

## #4b — Gate-strip convergence (DONE, verified)

The reconcile card's decision-gate (`renderDecisionBrief`) was re-deriving a verdict-reaction label in JS
("READY TO VALIDATE" for replicated, "HOLD FOR PAIRED VALIDATION" for discordant). This CONFLATED two
distinct questions: the gate is the "should it advance?" axis, which is `brief.recommendation` (dossier-
informed, verdict-INDEPENDENT) — NOT the verdict. The JS gate was scientifically wrong: it labelled every
replicated gene "advance" even when the dossier says `validate_first` (e.g. ATP1B3), overclaiming an
advancement the drug/disease evidence hasn't justified.

Fixed: the gate now renders `brief.recommendation` (via the existing `RECOMMENDATION_LABEL` map + a new
`RECOMMENDATION_GATE_CLASS` colour map), reason = `brief.recommendation_rationale`, next-step = a short
recommendation-keyed phrase. Per-condition (reads `row.decision_brief`). Row-based fallback when no brief.
Verified: ATP1B3(replicated)→"Validate first" (was wrongly "advance"); TSC1(discordant)→"Hold — weak target";
BATF(discordant)→"Validate first" — BATF & TSC1 are both discordant but get DIFFERENT stances because their
dossiers differ, which is the whole point of the axis. Caught + fixed a layout regression (long rationale in a
narrow gate column wrapped one-word-per-line — moved the full objective out of the gate's next-step slot).

## Scope discipline (hackathon)
Minimum honest ship = **#1 (done)**. #2 is the highest-value single addition (positive control makes every
null honest). Full #3 is the "10-star" version but is the largest lift — gate it on time remaining. Do NOT
half-build #3 (verdict-specific templates with no control) — that reintroduces the exact class of bug we just
fixed.
