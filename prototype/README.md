# Prototype — PRE-KICKOFF exploration (NOT the hackathon submission)

> ⚠️ **Hackathon rule: new work only, code started at kickoff (Tue Jul 7, 12:00 PM ET).**
> Everything in this `prototype/` dir is throwaway exploration written *before* the build
> to de-risk the data and validate the design. It is NOT the submitted project. The real,
> submitted tool will be built from scratch at kickoff (likely under `src/`), reusing only
> the *lessons* here — not this code. Downloading public data (in `../data/`) is prep and
> is allowed pre-kickoff.

## What this proves

A laptop-native "Target Triage" pipeline that ranks druggable, disease-linked T-cell
regulators from the Marson Perturb-seq screen, and an adversarial verifier that stress-tests
each candidate with **real computed evidence** — no 16.8 GB download, no GPU.

## Files

| File | Role |
|---|---|
| `rank_sketch.py`  | 5-step ranking: gate → score → fold → **Open Targets overlay** → rank |
| `opentargets.py`  | Live Open Targets GraphQL client (batched, cached to `ot_cache.json`) |
| `verifier.py`     | Adversarial verifier — Tier A (DE summary) + Tier B (donor/guide robustness) + Tier C3 (held-out screens) |
| `held_out.py`     | Tier C3: is a pick corroborated by an independent screen (Schmidt2022 / Freimer2022)? |
| `ot_cache.json`   | Cached Open Targets results (7,195 genes) so re-runs are instant |

## Data (in `../data/`, all public)

- `marson_perturbseq/` — the screen's suppl tables (DE_stats 4.6 MB is the core; sgRNA lib; sample meta)
- `robustness/` — precomputed cross-donor + cross-guide correlations (from the emdann analysis repo)
- `external_screens/` — Schmidt2022 + Freimer2022 independent CRISPR screens (held-out validation)

## Run

```bash
cd prototype
python3 verifier.py      # verdicts on a demo panel (heroes + controls + genes that should fail)
python3 held_out.py      # just the held-out screen corroboration
python3 rank_sketch.py   # full ranking (uses cached Open Targets; first run ~3 min uncached)
```

## Key findings (locked into ../ARCHITECTURE.md)

- **Data path**: the summary CSV (4.6 MB) + repo robustness tables (< 1 MB) + 2 screens make the
  whole tool laptop-native. The 44/16.8 GB h5ad files are NOT needed for v1.
- **Verifier has teeth**: A1BG (noise) → REJECT; DGKA/PTPN22 (strong KD, no downstream) → weak;
  heroes → PROMOTE (corroborated) with real cross-donor/guide correlations + independent-screen hits.
- **CBLB is the strongest hero**: corroborated in 2 Schmidt readouts, cross-donor 0.75 / cross-guide 0.92,
  druggable + MS-linked + NX-1607 in Phase 1. PTPN2 is real but NOT in the held-out screens — its
  independent evidence is clinical (ABBV-CLS-484) + Open Targets genetics, not screen-based.
- **Open Targets overlay** promotes modest-impact druggable genes (PTPN2 raw #812 → actionable #45).
- **The verifier outputs numbers; the real tool's Claude agent turns them into the verdict + narrative.**
