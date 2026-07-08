"""The controls-first gate — the tool's onboarding validator for ANY screen.

The reusable-instrument promise rests here: before a scientist trusts a single novel
pick, the tool must recover the KNOWN biology of their screen. Each screen declares
its own positive controls (see ScreenSchema.controls); this gate checks that each one
shows a real, significant knockdown in that screen. If the controls don't reproduce,
the load/mapping is wrong or the screen is unsuitable — and the tool says so, loudly,
instead of handing back a plausible-looking but untrustworthy shortlist.

Screen-agnostic by construction: it runs on whatever signals the screen provides. A
control "passes" if it has a significant clean knockdown of real strength — the one
criterion every screen can support. (Breadth-based tiering is a Marson refinement in
the eval; the core gate here is the portable contract.)
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import GeneRecord, load_screen
from .schema import MARSON, ScreenSchema

MIN_EFFECT = 2.0   # |effect| floor for a "real" knockdown (portable across screens)


@dataclass(frozen=True)
class ControlResult:
    gene: str
    found: bool
    passed: bool
    best_effect: float
    condition: str | None
    reason: str


@dataclass(frozen=True)
class GateResult:
    screen: str
    passed: bool
    results: tuple[ControlResult, ...]

    @property
    def summary(self) -> str:
        n = sum(r.passed for r in self.results)
        return f"{n}/{len(self.results)} controls passed"


def evaluate_control(gene: str, by_gene: dict[str, GeneRecord]) -> ControlResult:
    record = by_gene.get(gene)
    if record is None:
        return ControlResult(gene, False, False, 0.0, None, "not in screen")
    sig = [
        p for p in record.by_condition.values()
        if p.significant and not p.offtarget and abs(p.effect_size) >= MIN_EFFECT
    ]
    if not sig:
        return ControlResult(gene, True, False, 0.0, None,
                             f"no significant clean KD with |effect|>={MIN_EFFECT}")
    best = max(sig, key=lambda p: abs(p.effect_size))
    return ControlResult(gene, True, True, round(abs(best.effect_size), 1), best.condition,
                         f"|effect|={abs(best.effect_size):.1f} in {best.condition}")


def run_controls_gate(schema: ScreenSchema = MARSON) -> GateResult:
    """Run the controls gate for a screen. This is what the UI/agent call to prove a
    screen loaded correctly before showing any novel picks."""
    by_gene = {r.gene: r for r in load_screen(schema)}
    results = tuple(evaluate_control(g, by_gene) for g in schema.controls)
    return GateResult(schema.name, all(r.passed for r in results), results)


if __name__ == "__main__":
    import sys
    from .schema import REGISTRY

    ok = True
    for name, schema in REGISTRY.items():
        gate = run_controls_gate(schema)
        print(f"\nCONTROLS GATE — {name} ({schema.description})")
        for r in gate.results:
            print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.gene:<8} {r.reason}")
        print(f"  => {gate.summary}", "PASS" if gate.passed else "FAIL")
        ok = ok and gate.passed
    sys.exit(0 if ok else 1)
