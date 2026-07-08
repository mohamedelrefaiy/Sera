# Project Working Memory

Project-local context for the **Built with Claude: Life Sciences** hackathon. Read this
first; deeper notes live in `memory/`. Task list is `docs/TASKS.md` (view/edit via
`docs/dashboard.html`). All prose docs and the HTML tools live under `docs/`.

## Me

- Solo builder. Strong in genomics / computational biology.
- Bandwidth: heavy but not full-time.
- Writes tasks as full natural-language sentences (no shorthand codes — see `memory/glossary.md`).
- Prefers: scope discipline, video-first demo, controls-first ("nothing is done until the
  positive-control eval passes"), incremental commits over bulk commits.

## People

One file per person under `memory/people/`. None recorded yet — add as collaborators,
judges, or lab contacts come up.

## Terms

Project vocabulary lives in `memory/glossary.md`. Started clean; grows as we go.

## Layout

- **`application/`** — the runnable app, self-contained: `src/` (the `target_triage`
  package), `data/` (screen CSVs), `web/` (served frontend), `eval/` (controls gate +
  tests), and its own `pyproject.toml`/`requirements.txt`. Install: `pip install -e application/`.
  Serve: `python application/src/target_triage/api/serve.py`. Tests: `pytest application/eval`.
- **`docs/`** — all prose + the HTML tools (task board, skills guide, research).
- Repo root also holds `CLAUDE.md`, `LICENSE`, and working dirs (`memory/`, `prototype/`).

## Projects

- **Target Triage** — the hackathon build. See `memory/projects/target-triage.md`,
  plus `docs/HACKATHON_PLAN.md` (phases + deadline) and `docs/ARCHITECTURE.md` (pipeline, upgrades).

## Frontend design (mandatory)

Any frontend/UI work — new pages, redesigns, or visual polish — MUST go through the
installed design-taste skills. Do not hand-roll UI without them.

- **Redesign / audit existing pages** → invoke `/redesign-existing-projects` first (it
  audits before changing, so it won't break live wiring), then apply.
- **Aesthetic direction** → layer in `/minimalist-ui` (clean editorial data UI — the
  right register for a genomics evidence tool; the data is the hero). Port its principles
  into the existing dark palette; do not flip pages to light mode.
- **New pages from scratch** → `/design-taste-frontend` (v2 default).
- Other variants available if a task calls for them: `/high-end-visual-design`,
  `/full-output-enforcement`, `/design-taste-frontend-v1` (legacy fallback).

Precedent: `web/run.html` was redesigned this way (two-column sticky rail + polish pass,
zero data touched). Keep that bar.

## Preferences

- **Deadline is real:** Monday **2026-07-13, 9:00 PM ET** — submit early evening, not the last hour.
- **Controls-first gate:** an item isn't "done" until its positive-control eval prints PASS.
- **Commit incrementally** with conventional-commit messages; never a single bulk commit (DQ risk).
- **Verify before claiming done** — if a check fails, say so with the output; never report a
  control as reproduced without printing the number.
- **New work only, MIT-licensed, fully open source.**
- Immutable data patterns, small focused files, explicit error handling.
