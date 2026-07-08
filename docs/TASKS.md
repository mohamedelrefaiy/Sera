# Tasks

> Plain-text source of truth. Edit here by hand, or use `dashboard.html` (drag cards
> between columns). Each task is a `- [ ]` line under a section heading. The dashboard
> reads these four `##` sections and writes changes back to this file.

## Active

> REFRAME (Tue): The TOOL is the product; the Marson dataset is just PROOF it works.
> Value = a reusable instrument (rank → adversarially verify → interrogate) that runs on
> ANY screen, reproducibly — not a one-off analysis. So it must WORK ON ANY SCREEN.
> Locked design: agent infers the column MAPPING (confirmable); the ANALYSIS stays
> deterministic (same shortlist every run); honest degradation when a signal is absent.

- [ ] ScreenSchema (immutable) + generic load_screen(path, schema) → GeneRecord; Marson becomes one registered schema
- [ ] Handle optional signals: breadth may be absent (Schmidt2022 has none), significance may be FDR+threshold not a bool
- [ ] Agent tool infer_schema(path): reads header+rows, proposes mapping, scientist confirms
- [ ] Prove reusability: register Schmidt2022 as a 2nd screen, run the SAME pipeline, its controls gate passes
- [ ] Screen picker in the UI (Marson / Schmidt2022 / upload-your-own)
- [ ] Live agent run + record fallback transcripts; README + 100-200w summary

## Waiting On

<!-- Tasks blocked on someone/something else. Note who/what and since when. -->

## Someday

- [ ] Stretch: second T-cell CRISPR screen as an independent cross-check
- [ ] Stretch: broaden druggable-genome + GWAS overlay beyond the committed core

## Done

- [x] Set up application/src/ Agent SDK package skeleton + .venv (claude-agent-sdk 0.2.111, pytest)
- [x] Data boundary: immutable loader for the Marson DE summary (application/src/target_triage/core/data.py)
- [x] Pure ranking logic over immutable records (application/src/target_triage/core/ranking.py)
- [x] **Controls-first gate PASSES 5/5** — RASA2/IL2RA/TNFAIP3 (broad) + CTLA4/FOXP3 (focused), two-tier by biological signature (application/eval/test_controls.py)
- [x] Adversarial verifier + evidence boundary; verifier gate PASSES (A1BG REJECT, heroes PROMOTE)
- [x] Open Targets + ClinicalTrials.gov clients in src/ with seeded caches (new work)
- [x] **Agent SDK loop**: 4 @tool wrappers + ClaudeAgentOptions; tool-layer gate PASSES (13/13 evals)
- [x] CLI entry `python -m target_triage` streams the agent's tool calls + reasoning
- [x] **Web app**: FastAPI backend (deterministic /api/shortlist + /api/target, SSE /api/chat) +
      two-panel UI (evidence ledger + agent console). Verified in preview: drawer shows NRAS's
      donor -0.1 rejection; chat degrades cleanly with no key; responsive. Package installable (pyproject).
