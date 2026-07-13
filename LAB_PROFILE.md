# Lab Profile v1 implementation contract

## Product outcome

Lab Profile lets a scientist save the bench capacity they normally have and reuse it when Concord proposes a discriminating experiment. It is laboratory context, not an AI persona: it constrains what the deterministic decision-brief builder may call feasible; it does not change the reconciliation verdict or instruct Claude to imitate a role.

The v1 promise is narrow: **Concord remembers which paired readouts, donor count, and time window this browser normally has, then clearly separates those defaults from temporary constraints for one experiment.**

## Existing source of truth

The implementation must extend the current constraints path rather than create a second planning system:

1. `target_triage/frontend/concord-app.html` already renders the “Tune to your bench” controls and requests a constrained brief.
2. `target_triage/api/app.py::_parse_constraints` converts the query parameters into `ExperimentConstraints`.
3. `target_triage/core/decision_brief.py::ExperimentConstraints` validates the closed readout set and non-negative donor/day counts.
4. `target_triage/core/decision_brief.py::_experiment_and_outcomes` deterministically selects a protein readout, checks paired RNA/protein feasibility and donor sufficiency, adapts the time course, and reports unmet requirements.
5. `build_decision_brief` receives those constraints without changing the code-computed concordance verdict.

The Lab Profile is therefore a frontend persistence and input-resolution layer. The resolved constraints sent to the API remain the only inputs that affect feasibility.

## V1 profile schema

Persist one active profile under a versioned browser-local key such as `concord.labProfile.v1`.

```json
{
  "schema_version": 1,
  "lab_name": "My lab",
  "readouts": ["qpcr", "facs"],
  "donors": 3,
  "days": 7
}
```

| Field | Type and validation | Purpose |
| --- | --- | --- |
| `schema_version` | integer, exactly `1` | Supports safe migration or reset if the browser contains an incompatible profile. |
| `lab_name` | trimmed string, 1–60 characters | Optional display label only; it never affects scientific logic or enters an LLM prompt. |
| `readouts` | unique array drawn only from `qpcr`, `elisa`, `facs`, `western` | Maps exactly to the current `READOUTS` closed set. At least one must be selected to save a useful profile. |
| `donors` | integer, 0–12 | The normal maximum donor capacity for a proposed experiment. Zero is valid and must not be replaced by a default. |
| `days` | integer, 0–60 | The normal available experimental window. Zero is valid and must not be replaced by a default. |

When no valid saved profile exists, the UI uses the current product defaults: `qpcr` + `facs`, 3 donors, 7 days. Defaults must be visibly described as product defaults, not as facts about the user’s lab.

## Persistence and privacy

- Store the profile in browser `localStorage`; do not add a backend profile endpoint or include it in chat-session persistence in v1.
- Show a short disclosure beside Save: “Saved only in this browser.”
- Provide “Reset profile,” which deletes the versioned key and restores product defaults.
- Do not store patient, donor, sample, protocol, credential, or free-text scientific data.
- Do not send `lab_name` to the API or to Claude. Send only the resolved `readouts`, `donors`, and `days` needed by the deterministic builder.
- Treat malformed JSON, a wrong schema version, unknown readouts, duplicate readouts, or out-of-range numbers as an invalid profile. Ignore it safely and show product defaults; never partially trust corrupted state.

Browser-local persistence intentionally means the profile does not follow a scientist to another browser/device and is shared with anyone using the same browser profile. The UI should state that limitation without implying account-level privacy or synchronization.

## Defaults and per-run overrides

The resolution order is explicit:

```text
validated per-run override > validated saved Lab Profile > product defaults
```

- “My Lab” edits and saves the persistent defaults.
- “Adjust for this experiment” starts prefilled from the saved profile, changes only the current plan request, and does not mutate the saved profile.
- The constrained plan displays its source: “Using My Lab defaults” or “Temporary overrides applied.”
- Provide “Restore lab defaults” within the per-run controls.
- A new gene/plan starts from the saved profile, not from the previous plan’s temporary overrides.
- Every constrained API request sends all three resolved parameters. Do not rely on partial query parameters, because the current parser treats omitted members of a partially supplied constraint set as zero/empty.

## Deterministic feasibility contract

Lab Profile changes experiment feasibility and adaptation only. It must never change:

- the concordance snapshot or verdict;
- screen comparability;
- target dossier or advanceability thresholds;
- explanation classes;
- citations.

For the current decision-brief builder, the resolved profile has these effects:

| Resolved constraint | Deterministic result |
| --- | --- |
| No `qpcr` | `feasible = false`; report that a transcript readout is required. |
| None of `facs`, `elisa`, or `western` | `feasible = false`; report that a protein readout is required. |
| Fewer than 3 donors | `feasible = false`; report donor insufficiency. |
| Fewer than 3 days | Remove the time course, add an adaptation, and retain temporal-feedback uncertainty. This alone does not make the plan infeasible. |
| Multiple protein readouts | Select deterministically in the existing order: FACS, then ELISA, then Western. |
| All paired-readout and donor requirements met | `feasible = true`; render the chosen readouts and any time-window adaptation returned by the builder. |

The frontend must render the API’s `feasible`, `unmet_requirements`, `adaptations`, `experiment`, and `remaining_uncertainty` fields. It must not duplicate or reinterpret these rules in JavaScript.

## UI scope

V1 adds:

- a “My Lab” entry point in the existing Concord workspace;
- a small editor for lab name, available readouts, usual donor capacity, and usual time window;
- save/reset actions and browser-local disclosure;
- a compact active-profile summary on decision-plan views;
- explicit “Adjust for this experiment” and “Restore lab defaults” behavior around the existing tuner.

The profile should be useful without requiring Claude or an API credential. Constraint changes continue to call the deterministic `/api/concordance/{gene}` path.

## Non-goals for v1

- Multiple saved labs, account sync, team sharing, permissions, or server persistence.
- Free-text prompt/persona editing.
- Storing donor identities, samples, experimental results, protocols, budgets, or regulated data.
- New readout tokens, instruments, cell models, perturbation methods, biosafety levels, plate capacity, or reagent inventory. These need corresponding typed backend fields and feasibility rules before appearing in the profile.
- Letting a profile alter screen verdicts, evidence, recommendation thresholds, or citations.
- Claiming a non-decisive substitute is decisive when required paired measurements or donors are unavailable.

## Acceptance criteria

1. With no saved profile, the Lab Profile editor and a new plan show `qpcr` + `facs`, 3 donors, and 7 days as product defaults.
2. Saving a valid profile survives a page reload in the same browser and sends no profile write to the server.
3. Reset removes the local key and restores product defaults.
4. An invalid or incompatible local payload cannot crash the app and is not sent to the API.
5. A new decision plan automatically resolves constraints from the saved profile and requests the constrained decision brief.
6. A temporary override changes the current plan, is visibly labeled, survives that plan’s re-render, and does not overwrite the saved profile.
7. Starting another plan uses the saved profile rather than the previous plan’s temporary override.
8. An ELISA-only profile returns and displays `feasible = false` with the missing transcript requirement; the concordance verdict is unchanged.
9. A `qpcr` + `facs`, 1-donor profile returns and displays donor insufficiency; the verdict is unchanged.
10. A `qpcr` + `elisa`, 3-donor, 2-day profile returns a feasible paired plan, drops the time course, and displays the remaining temporal uncertainty.
11. Unknown readout tokens and invalid numbers are rejected before saving; API validation remains the final authority and errors are shown rather than silently reverting.
12. Automated tests cover profile parsing/validation, precedence, reset, reload, temporary overrides, and the three existing backend feasibility cases.

## Integration risks to address

- **Partial-parameter zeroing:** `_parse_constraints` turns missing fields into empty readouts/zero donors/zero days once any constraint is present. Always send the complete resolved set, and preserve numeric zero explicitly instead of using truthy fallback logic.
- **UI state is currently ephemeral:** the inline tuner stores active values only on a transient `decision_brief.__constraints` property and replaces the plan DOM after re-planning. Profile state must live outside a rendered turn, with each turn holding only its resolved per-run snapshot.
- **The tuner is plan-only:** normal gene reconciliation requests currently fetch `/api/concordance/{gene}` without constraints. The product must define whether only explicit plan views are lab-adapted (recommended for v1) and label unconstrained reconciliation views accordingly.
- **Backend scope is narrower than the desired future profile:** perturbation is fixed to arrayed CRISPRi and conditions are fixed to matched stimulated CD4+ T cells. Adding perturbation methods or biological models to the UI before typed backend support would falsely imply adaptation.
- **Frontend contains fallback experiment templates:** plan rendering can fall back to verdict-keyed static text when `decision_brief` is unavailable. A lab-adapted view must clearly mark fallback content as unconstrained and must not present it as adapted to the saved profile.
- **Minimum versus actual capacity:** current plan prose often says “at least 3 donors” rather than echoing the scientist’s actual donor capacity. Avoid claiming that all available donors will be used unless the experiment schema gains an explicit planned donor count.
- **Readout semantics differ:** FACS, ELISA, and Western are all treated as interchangeable protein availability for feasibility, although they answer different biological questions. V1 should preserve the builder’s current claims and avoid stronger assay-specific conclusions; richer semantics require backend changes and tests.
- **Single active browser profile:** local storage is origin- and browser-profile-specific, can be cleared, and may be visible to another person using the same browser profile. The disclosure must not imply secure account storage.

## Verification target

Keep the existing decision-brief gates passing, especially `target_triage/eval/test_decision_brief.py` and `target_triage/eval/test_decision_brief_api.py`. Add focused frontend tests if a browser test harness is introduced; otherwise isolate profile parsing and constraint resolution into testable functions and include a manual reload/reset/override checklist.
