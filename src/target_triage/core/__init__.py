"""Domain core — the deterministic pipeline, free of any Claude/LLM or web concern.

Load the screen, rank by impact, adversarially verify, assemble the shortlist, and
gate on positive controls. Everything here is pure computation over the screen plus
cached external scores; the api/, agent/, and llm/ layers depend on this, never the
other way around.
"""
from __future__ import annotations

from .controls import ControlResult, GateResult, run_controls_gate
from .data import CONDITIONS, GeneRecord, Perturbation, load_perturbations, load_screen
from .evidence import Evidence, ScreenHit, load_evidence
from .ranking import ScoredGene, rank_by_impact, significant_records
from .schema import MARSON, REGISTRY, SCHMIDT2022, ScreenSchema, get_schema
from .shortlist import SPOTLIGHT, compute_shortlist, get_target
from .verify import DEFAULT, Thresholds, Verdict, verify

__all__ = [
    "ControlResult",
    "GateResult",
    "run_controls_gate",
    "CONDITIONS",
    "GeneRecord",
    "Perturbation",
    "load_perturbations",
    "load_screen",
    "Evidence",
    "ScreenHit",
    "load_evidence",
    "ScoredGene",
    "rank_by_impact",
    "significant_records",
    "MARSON",
    "REGISTRY",
    "SCHMIDT2022",
    "ScreenSchema",
    "get_schema",
    "SPOTLIGHT",
    "compute_shortlist",
    "get_target",
    "DEFAULT",
    "Thresholds",
    "Verdict",
    "verify",
]
