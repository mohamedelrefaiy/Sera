# Plan-Orchestrate Result

**Plan**: `HACKATHON_PLAN.md`
**Lang**: `python` (26 `.py` files; no `torch` → `pytorch=false`)
**ECC mode**: ambiguous install — plugin `everything-claude-code` **and** bare `~/.claude/agents/` both present. Two command forms given per step; use whichever your `/help` lists as registered. If `/ecc:orchestrate` exists → use the plugin lines. If only `/orchestrate` → use the legacy lines.
**Steps**: 8 (P0–P7); P0 and P7 are prep/buffer with no build code → **skip** for orchestration.
**Scope**: all

> The `<lang>-reviewer` resolves to `python-reviewer`; `<lang>-build-resolver` resolves to `build-error-resolver` (no torch).

---

## Steps overview

| # | Phase | Primary intent | Tag(s) | Chain (bare names) |
|---|---|---|---|---|
| 0 | P0 · Prep + de-risk data | Reading/setup, no code | — | **SKIP** (no code) |
| 1 | P1 · Kickoff + eval-first scaffold | Build control eval FIRST, then skeleton | impl | `tdd-guide,python-reviewer` |
| 2 | P2 · Analysis agent end-to-end | Build ranking agent; controls reproduce | impl + test | `tdd-guide,python-reviewer,e2e-runner` |
| 3 | P3 · Computation-grounded verifier + tool | Build adversarial verifier (real p-value + Open Targets) + UI/CLI | impl + review | `tdd-guide,python-reviewer,code-reviewer` |
| 4 | P4 · Held-out prediction + validate | Add GWAS overlay; confirm blind pick vs external source | impl | `tdd-guide,python-reviewer` |
| 5 | P5 · Polish + FREEZE | README repro steps, written summary, demo-path fixes | docs | `doc-updater` |
| 6 | P6 · Demo video | Script/record/edit; no code | — | **SKIP** (no code) |
| 7 | P7 · Buffer + submit | Fix breakage, submit | — | **SKIP** (buffer only) |

**Chain rationale**
- **Step 1** — `impl` (build the eval + skeleton). Ends on a reviewer (`python-reviewer`) per rule 10. Controls-first: the eval IS the deliverable here.
- **Step 2** — `impl` primary + `test` secondary ("reproduce", "end-to-end"). `e2e-runner` gates the reproduction claim. Chain length 3, under the ≤4 cap.
- **Step 3** — `impl` primary + `review` secondary ("adversarial verifier", "survived scrutiny"). Ends on `code-reviewer` after `python-reviewer` — the verifier logic is the credibility core, so it gets a second review pass. `security-reviewer` intentionally omitted (no auth/PII/secrets in scope).
- **Step 4** — `impl` (add overlay + external cross-check). Reviewer tail per rule 10. The held-out-reveal logic is correctness-critical (a wrong "we predicted it" claim is fatal on camera) → `python-reviewer` is the right gate.
- **Step 5** — `docs` primary. README reproducibility is a first-class deliverable per the plan → `doc-updater`.

---

## Step 1 — P1 · Kickoff + eval-first scaffold

**Plugin form:**
```
/ecc:orchestrate custom "ecc:tdd-guide,ecc:python-reviewer" "[Plan: HACKATHON_PLAN.md#step-1] Build the positive-control eval FIRST, before the tool around it: a check that scores whether known T-cell regulators (RASA2, IL2RA, CTLA4, FOXP3, TNFAIP3) move known gene programs in the expected direction from the Marson CD4+ Perturb-seq pseudobulk/DE tables. Then a minimal skeleton that makes one control pass. Acceptance: (1) eval runs on the loaded DE tables and prints PASS/FAIL per control; (2) at least RASA2 and IL2RA print PASS with the direction shown; (3) pytest covers the eval scoring logic. Commit incrementally."
```

**Legacy form:**
```
/orchestrate custom "tdd-guide,python-reviewer" "[Plan: HACKATHON_PLAN.md#step-1] Build the positive-control eval FIRST, before the tool around it: a check that scores whether known T-cell regulators (RASA2, IL2RA, CTLA4, FOXP3, TNFAIP3) move known gene programs in the expected direction from the Marson CD4+ Perturb-seq pseudobulk/DE tables. Then a minimal skeleton that makes one control pass. Acceptance: (1) eval runs on the loaded DE tables and prints PASS/FAIL per control; (2) at least RASA2 and IL2RA print PASS with the direction shown; (3) pytest covers the eval scoring logic. Commit incrementally."
```

---

## Step 2 — P2 · Analysis agent works end-to-end

**Plugin form:**
```
/ecc:orchestrate custom "ecc:tdd-guide,ecc:python-reviewer,ecc:e2e-runner" "[Plan: HACKATHON_PLAN.md#step-2] Build the analysis agent that fans out over perturbations, scores gene programs, and ranks druggable regulator candidates end-to-end. Acceptance: (1) one command ranks candidates from the DE tables and writes a ranked list; (2) the P1 control eval still PASSES (RASA2/IL2RA reproduce and land high in the ranking); (3) an end-to-end run from raw tables to ranked output completes without manual steps. Do not touch the P1 eval scoring logic."
```

**Legacy form:**
```
/orchestrate custom "tdd-guide,python-reviewer,e2e-runner" "[Plan: HACKATHON_PLAN.md#step-2] Build the analysis agent that fans out over perturbations, scores gene programs, and ranks druggable regulator candidates end-to-end. Acceptance: (1) one command ranks candidates from the DE tables and writes a ranked list; (2) the P1 control eval still PASSES (RASA2/IL2RA reproduce and land high in the ranking); (3) an end-to-end run from raw tables to ranked output completes without manual steps. Do not touch the P1 eval scoring logic."
```

---

## Step 3 — P3 · Computation-grounded verifier + tool

**Plugin form:**
```
/ecc:orchestrate custom "ecc:tdd-guide,ecc:python-reviewer,ecc:code-reviewer" "[Plan: HACKATHON_PLAN.md#step-3] Add the adversarial verifier that reports REAL numbers, not opinions: a permutation/label-shuffle test plus a donor-holdout to produce an empirical p-value per candidate, AND an Open Targets DB cross-check. It must REJECT failing candidates and keep rejections visible. Wrap in a CLI a scientist runs untouched; output = shortlist + per-target report (p-value, external evidence, challenges survived and rejected). Acceptance: (1) verifier emits a real permutation p-value and a real Open Targets result per candidate; (2) at least one weak hit is rejected and shown as rejected; (3) reproducible from a README command. Controls from P1/P2 must still PASS."
```

**Legacy form:**
```
/orchestrate custom "tdd-guide,python-reviewer,code-reviewer" "[Plan: HACKATHON_PLAN.md#step-3] Add the adversarial verifier that reports REAL numbers, not opinions: a permutation/label-shuffle test plus a donor-holdout to produce an empirical p-value per candidate, AND an Open Targets DB cross-check. It must REJECT failing candidates and keep rejections visible. Wrap in a CLI a scientist runs untouched; output = shortlist + per-target report (p-value, external evidence, challenges survived and rejected). Acceptance: (1) verifier emits a real permutation p-value and a real Open Targets result per candidate; (2) at least one weak hit is rejected and shown as rejected; (3) reproducible from a README command. Controls from P1/P2 must still PASS."
```

---

## Step 4 — P4 · Held-out prediction + validate

**Plugin form:**
```
/ecc:orchestrate custom "ecc:tdd-guide,ecc:python-reviewer" "[Plan: HACKATHON_PLAN.md#step-4] Add the druggable-genome + immune-GWAS overlay to surface non-obvious candidates, then the held-out beat: the pipeline flags a top pick WITHOUT using clinical knowledge, and a separate confirmation step checks it against an independent source the pipeline never used (Open Targets tractability, a 2nd T-cell CRISPR screen, or trial status). Acceptance: (1) overlay ranks at least 3 druggable, disease-linked candidates; (2) the top blind pick is confirmed against an external source not used in ranking, and the report states predicted-then-confirmed explicitly; (3) honest limitations (donor robustness, context confounds) are recorded. Do not let the confirmation source leak into the ranking inputs."
```

**Legacy form:**
```
/orchestrate custom "tdd-guide,python-reviewer" "[Plan: HACKATHON_PLAN.md#step-4] Add the druggable-genome + immune-GWAS overlay to surface non-obvious candidates, then the held-out beat: the pipeline flags a top pick WITHOUT using clinical knowledge, and a separate confirmation step checks it against an independent source the pipeline never used (Open Targets tractability, a 2nd T-cell CRISPR screen, or trial status). Acceptance: (1) overlay ranks at least 3 druggable, disease-linked candidates; (2) the top blind pick is confirmed against an external source not used in ranking, and the report states predicted-then-confirmed explicitly; (3) honest limitations (donor robustness, context confounds) are recorded. Do not let the confirmation source leak into the ranking inputs."
```

---

## Step 5 — P5 · Polish + FREEZE

**Plugin form:**
```
/ecc:orchestrate custom "ecc:doc-updater" "[Plan: HACKATHON_PLAN.md#step-5] Write the README with exact reproduction steps so a stranger can re-run the analysis from scratch, and a 100-200 word written summary stating the FINDING (controls reproduced + N candidates surviving scrutiny). Acceptance: (1) README lists every command from data download to final report, runnable top-to-bottom; (2) written summary is 100-200 words and names the controls reproduced and the candidate count; (3) no feature changes — docs and demo-path fixes only."
```

**Legacy form:**
```
/orchestrate custom "doc-updater" "[Plan: HACKATHON_PLAN.md#step-5] Write the README with exact reproduction steps so a stranger can re-run the analysis from scratch, and a 100-200 word written summary stating the FINDING (controls reproduced + N candidates surviving scrutiny). Acceptance: (1) README lists every command from data download to final report, runnable top-to-bottom; (2) written summary is 100-200 words and names the controls reproduced and the candidate count; (3) no feature changes — docs and demo-path fixes only."
```

---

## Skipped steps

- **P0 (Prep + de-risk data)** — reading, data download, Discord setup. No build code → not an orchestration step.
- **P6 (Demo video)** — script/record/edit. Not code.
- **P7 (Buffer + submit)** — fix breakage and submit. Reactive, not a planned build chain.

---

## How to use this

1. Check `/help` for whether `/ecc:orchestrate` or `/orchestrate` is the registered command.
2. Run steps **in order** — each assumes the prior step's output exists (the P1 eval must keep passing through P2–P4).
3. Paste one step, let the chain finish, verify its acceptance criteria (especially "controls still PASS"), then move to the next.
4. **Solo-builder reality check:** these chains spawn multi-agent runs. On your deadline, treat them as optional accelerators, not a required path — running Step 1 directly by hand is often faster than orchestrating it. The highest-value one to orchestrate is **Step 3** (the verifier), where the tdd → review → second-review chain buys real rigor on the credibility-critical code.
