# Tasks

> Plain-text source of truth. Edit here by hand, or use `dashboard.html` (drag cards
> between columns). Each task is a `- [ ]` line under a section heading. The dashboard
> reads these four `##` sections and writes changes back to this file.

## Active

- [ ] Do a real agent run end-to-end and capture the transcript for the demo
- [ ] Emit the shortlist artifact (JSON + per-target markdown reports) from the agent result
- [ ] README with exact reproduction steps + written summary (100-200w)

## Waiting On

<!-- Tasks blocked on someone/something else. Note who/what and since when. -->

## Someday

- [ ] Stretch: second T-cell CRISPR screen as an independent cross-check
- [ ] Stretch: broaden druggable-genome + GWAS overlay beyond the committed core

## Done

- [x] Set up src/ Agent SDK package skeleton + .venv (claude-agent-sdk 0.2.111, pytest)
- [x] Data boundary: immutable loader for the Marson DE summary (src/target_triage/data.py)
- [x] Pure ranking logic over immutable records (src/target_triage/ranking.py)
- [x] **Controls-first gate PASSES 5/5** — RASA2/IL2RA/TNFAIP3 (broad) + CTLA4/FOXP3 (focused), two-tier by biological signature (eval/test_controls.py)
- [x] Adversarial verifier + evidence boundary; verifier gate PASSES (A1BG REJECT, heroes PROMOTE)
- [x] Open Targets + ClinicalTrials.gov clients in src/ with seeded caches (new work)
- [x] **Agent SDK loop**: 4 @tool wrappers + ClaudeAgentOptions; tool-layer gate PASSES (13/13 evals)
- [x] CLI entry `python -m target_triage` streams the agent's tool calls + reasoning
