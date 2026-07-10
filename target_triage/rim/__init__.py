"""Concord's peripheral agent layer -- agents on the RIM, deterministic instrument at the hub.

Named `rim/` (not `agents/`) for two reasons. It cannot be confused with the existing singular
`agent/` package, which is the Target Triage SDK loop. And it puts the architectural claim in
the import path: `from target_triage.rim.ingest import ...` tells you at the call site that this
code is peripheral. Nothing in `core/` may import from here.

Three modules, all at a boundary, none able to touch the verdict:

  adapters.py   pure, no model. Turns a real screen file into canonical rows. The row-producers
                that make "two hardcoded loaders" into "N screens, one schema".

  ingest.py     UPSTREAM of the core (Node A). Proposes how an arbitrary screen file maps onto
                the canonical axes. Its proposal is gated by core.canonical.validate() before a
                single row reaches the concordance builder. Agent proposes; validator disposes.

  interpret.py  DOWNSTREAM of the core (Node B). Reads a finished verdict and attaches cited,
                labelled mechanistic hypotheses plus one distinguishing experiment. Structurally
                forbidden from writing back: eval/test_writeback_invariant.py asserts the verdict
                table is hash-identical with this node on and off.

Neither node is imported by `core/` or `pipeline/02_build_concordance.py`. That is not an
accident of layering -- it is the guarantee. The dependency arrow only ever points inward.
"""
