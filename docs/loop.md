# Target Triage — default self-paced loop

You are iterating on **Target Triage**, a Claude Code agent that mines the Marson
genome-scale CD4+ T-cell Perturb-seq to rank druggable regulators of T-cell
activation, with an adversarial verifier that stress-tests each candidate.

Read `HACKATHON_PLAN.md` (phases + deadline) and `ARCHITECTURE.md` (the pipeline,
the two core upgrades, the controls) before deciding what to do. Respect the
**controls-first** rule: nothing is "done" until the positive-control eval passes.

Each iteration, work through this list **in order** and act on the FIRST item that
is not yet satisfied. Do the smallest useful increment, verify it, then stop — the
loop will call you again.

1. **Repo hygiene.** Is there an initialized git repo with an MIT license and at
   least one commit today? If not, the priority is a clean scaffold + incremental
   commit (the plan warns a single bulk commit risks DQ). Never bulk-commit; make a
   focused commit with a conventional message for whatever this iteration produced.

2. **Data is loadable.** Can the code load the pre-computed pseudobulk + DE tables
   (`GWCD4i.pseudobulk_merged.h5ad`, `GWCD4i.DE_stats.h5ad`) and pull one
   perturbation's DE? If not, that's the task. Do NOT touch the 22M-cell raw pipeline.

3. **Control eval passes (the gate).** Does a runnable check confirm the known
   regulators (RASA2, IL2RA, CTLA4, FOXP3, TNFAIP3) move the expected T-cell
   programs in the expected direction, and PRINT a PASS/FAIL per control? This is
   the eval that must exist before the tool is built around it. If it doesn't pass,
   that is the whole job this iteration.

4. **Analysis agent end-to-end.** Does the analysis agent fan out over perturbations,
   score gene programs (decoupler/gseapy over IL2/IFNG/cytokine sets), and emit a
   ranked candidate list? Extend it only once the controls reproduce.

5. **Computation-grounded verifier (Upgrade 2).** For each top candidate, does the
   verifier run REAL checks — donor-holdout, a permutation/label-shuffle test →
   empirical p-value, AND an Open Targets query — reject failures, and keep the
   rejected ones visible in the output? Numbers and sources, never LLM opinion.

6. **Held-out confirmation (Upgrade 1).** For the top surviving pick, is it checked
   against a source the pipeline never used as input (Open Targets tractability, a
   2nd T-cell CRISPR screen, or trial status) to land the "we predicted it and were
   right" beat?

7. **Reproducibility.** Does the README let a stranger re-run the analysis and get
   the same shortlist? Treat this as a first-class deliverable by P3, not a bolt-on.

8. **Nothing above is pending.** Run the full test/eval suite, report the current
   state against the phase in `HACKATHON_PLAN.md`, and **end the loop** (schedule no
   further wakeup). Do not invent scope beyond the committed upgrades; stretch items
   (3 and 4 in the architecture) are only for when genuinely ahead of schedule.

## Rules that bound every iteration
- **New work only, MIT-licensed, fully open source.** No code/data you lack rights to.
- **Verify before claiming done.** If a check fails, say so with the output. Never
  report a control as reproduced without printing the number.
- **Immutable data, small focused files** (per the coding-style rules), errors handled
  explicitly.
- **Commit incrementally** with a conventional-commit message after each real increment.
- **Watch the token budget.** Prefer the smallest increment that moves one item; don't
  refactor working code for polish while an earlier item is still failing.
