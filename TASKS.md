# Tasks

> Plain-text source of truth. Edit here by hand, or use `dashboard.html` (drag cards
> between columns). Each task is a `- [ ]` line under a section heading. The dashboard
> reads these four `##` sections and writes changes back to this file.

## Active

- [ ] Port verify.py into src/ (gate/score/bonus checks over immutable records)
- [ ] Add Open Targets + ClinicalTrials.gov clients to src/clients/ as new work
- [ ] Build the @tool wrappers + Claude Agent SDK loop (the agent orchestrates the tools)
- [ ] CLI entry (python -m target_triage) that runs the agent and writes the shortlist + reports

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
