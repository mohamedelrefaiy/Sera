# Tasks

> Plain-text source of truth. Edit here by hand, or use `dashboard.html` (drag cards
> between columns). Each task is a `- [ ]` line under a section heading. The dashboard
> reads these four `##` sections and writes changes back to this file.

## Active

> PIVOT (Tue): Target Triage is a TOOL a scientist drives, not just an agent that prints.
> Web app built + verified in preview. Deterministic shortlist always works; chat degrades cleanly.

- [ ] Do a real agent run through the web chat (needs API key) + capture transcript for the demo
- [ ] Record 2-3 canonical chat transcripts as scripted fallback for the demo
- [ ] Sync panels: agent re-rank/filter should update the table (chat ↔ table two-way)
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
- [x] **Web app**: FastAPI backend (deterministic /api/shortlist + /api/target, SSE /api/chat) +
      two-panel UI (evidence ledger + agent console). Verified in preview: drawer shows NRAS's
      donor -0.1 rejection; chat degrades cleanly with no key; responsive. Package installable (pyproject).
