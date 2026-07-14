# Sera interface design

Sera presents experimental evidence as a scientific decision brief. The interface
should read like a concise journal figure and its accompanying interpretation:
ordered, restrained, and legible on the first pass.

## Scientific text alignment

Long-form scientific prose uses one shared reading measure across the application.
This applies to the How It Works narrative and to generated chat interpretations.

- Set body-copy blocks to a maximum width of `78ch`.
- Align headings, decks, and narrative paragraphs to the same left content edge.
- Keep prose left-aligned with a natural ragged right edge; do not justify it.
- Use `text-wrap: pretty` for paragraphs and `text-wrap: balance` only for short
  headings.
- Let text wrap naturally. Do not insert manual line breaks to correct a single
  viewport.
- Keep structured evidence cards, tables, and diagrams on their own container
  width; the prose measure must not make these components narrower.
- Below the mobile breakpoint, use the available width and retain the page's
  horizontal padding rather than enforcing a fixed text width.

The current implementation uses `78ch` for `.how-lede`, `.how-p`, and
`.how-thesis-copy`. Chat decision briefs define `--report-copy-width: 78ch` on
`.brief-shell`; section decks and interpretation prose consume that variable.
Generic chat prose also uses a `78ch` maximum.

When adding a new scientific narrative block, reuse this measure instead of
introducing a local `60ch`, `66ch`, or `68ch` cap. A narrower measure is appropriate
only for compact component copy such as labels, captions, or decision-card notes.

## Review checklist

Before merging a text-layout change, verify that:

1. Related narrative sections begin on the same left axis.
2. Paragraphs share the `78ch` reading measure and wrap cleanly on the right.
3. Headings are balanced without forcing body copy into centered or justified text.
4. Evidence components retain their intended grid or full-container width.
5. Desktop and mobile layouts do not introduce clipped text or horizontal scrolling.
