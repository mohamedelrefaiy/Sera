# Sera design guidelines

Sera is an evidence workspace for reconciling mRNA and protein screens. It should feel like scientific decision infrastructure: calm, precise, traceable, and suitable for repeated use by research teams. The interface must not resemble a generic chat product or a decorative AI dashboard.

## Design principles

1. **Evidence before ornament.** Color, elevation, and emphasis communicate assay meaning, state, or provenance. They are not decoration.
2. **One coherent workspace.** Navigation, conversation, figures, evidence cards, and artifacts use the same spacing, surface, and border system.
3. **The conclusion is earned.** Present the user question, agent activity, measured comparison, interpretation, and provenance in that order.
4. **Scientific claims remain inspectable.** Agent activity and source history persist after completion. Dense detail may collapse, but it must not disappear.
5. **Progressive disclosure over noise.** Show the conclusion and primary figure first. Keep reasoning traces, detailed QC, and supporting sources one interaction away.

## Color system

- Workspace background: `#101519`
- Navigation background: `#0C1115`
- Primary surface: `#171D22`
- Elevated surface: `#1D252B`
- Primary text: `#F0F3F2`
- Secondary text: `#AEB9B8`
- Muted text: `#72807F`
- Hairline border: `#263137`
- Strong border: `#36454B`
- Structural accent: `#4BAF9B`
- mRNA data: `#4FC7A5`
- Protein data: `#E07BB6`
- Caution or discordance: `#D8A85A`

The structural teal is used for focus, active navigation, running status, and primary actions. mRNA green and protein magenta are reserved for measured data. Do not introduce additional decorative accent colors or gradients.

## Typography

Sera uses three intentional voices:

- **IBM Plex Sans:** navigation, controls, labels, buttons, and general product UI.
- **Source Serif 4:** scientific interpretation, narrative answers, figure captions, and conclusions.
- **IBM Plex Mono:** measurements, provenance, gene symbols, activity traces, statuses, and compact metadata.

Narrative text should normally use `17px` with approximately `1.65–1.7` line height. Use a comfortable evidence-column measure of roughly `72–78ch`; do not constrain responses to a narrow chat-bubble width. Use tabular numerals for quantitative values.

## Layout and spacing

- Desktop shell: navigation, central evidence workspace, optional artifact rail.
- Navigation width: about `248px`.
- Artifact rail width: about `344px`.
- Primary response width: up to `840px`, responsive to the available center column.
- Response sections use a `32px` vertical rhythm.
- Primary figures and evidence blocks align to the narrative column and may use its full width.
- Cards use `8–10px` radii. Small controls use `5–7px` radii. Avoid pill shapes except compact statuses.
- At narrower desktop widths, hide the artifact rail before compressing the scientific content.

## Agent responses

Each response should follow this hierarchy when the content is available:

1. User question
2. Persistent agent activity disclosure
3. Direct interpretation
4. Primary assay figure or deterministic evidence panel
5. Verdict and biological meaning
6. Quality, genetics, and druggability details
7. Provenance and follow-up actions

Write direct scientific prose. Lead with the answer, then qualify it with the evidence. Avoid conversational filler, repeated summaries, and vague claims. Paragraphs should use the available evidence column while retaining readable line length.

## Agent activity

- Activity is a persistent `<details>` disclosure, not an ephemeral loader.
- Keep it open while the agent is working.
- Collapse it automatically after completion, but never remove completed steps.
- The summary shows a live state such as `Working` and a settled state such as `3 steps complete`.
- Users can expand or collapse it with mouse, touch, or keyboard.
- Running steps use the structural accent and a restrained pulse. Completed steps remain muted but legible. The deterministic verdict may retain stronger emphasis.
- Do not expose internal framework discovery or irrelevant implementation details.

## Figures and data panels

- A primary figure is an evidence surface, not an attachment card.
- Figures use the full response width when space permits.
- Keep plot backgrounds darker than surrounding surfaces and borders quiet but visible.
- Titles identify figure number, gene, phenotype, and condition.
- Captions state comparison, units, significance encoding, and interpretation without repeating the entire response.
- mRNA and protein channels use their assigned colors consistently across figures, legends, and evidence rows.
- Use monospace for axes, values, assay labels, and provenance; use serif for captions and scientific interpretation.

## Interaction and accessibility

- Every interactive control has hover, pressed, focus-visible, and disabled states.
- Use native controls when possible, including `<details>` for disclosure.
- Minimum target size is `32px`; primary controls should be at least `40px` high.
- Never encode assay identity, verdict, or significance by color alone.
- Respect `prefers-reduced-motion`.
- Avoid horizontal page overflow at all supported breakpoints.

## Content and component rules

- Prefer sentence case for labels and headings.
- Gene symbols and machine states may use uppercase or monospace conventions.
- Use borders and background shifts before adding shadows.
- Reserve stronger elevation for the composer and primary scientific figure.
- Do not add generic dashboard cards when spacing or a divider communicates the hierarchy.
- Keep provenance visible but quiet. It should support trust without competing with the conclusion.

## Review checklist

- Does the page have one obvious scientific conclusion?
- Are assay colors used only for data?
- Can the complete activity trace be reopened after the response finishes?
- Do prose, figures, and evidence panels share a clear alignment?
- Is the response wide enough to use the workspace without becoming hard to read?
- Are units, conditions, significance, and sources inspectable?
- Are keyboard focus and reduced-motion behavior preserved?
- Is there any unnecessary card, badge, color, or repeated explanation that can be removed?
