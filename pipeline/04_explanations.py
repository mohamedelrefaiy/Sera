"""Phase 4 · step 04 — precompute GROUNDED Claude explanations for the "why this verdict" panel.

Claude receives a JSON record of MEASURED values (verdict + z/q per channel + directions) plus a
one-line gene annotation, and writes a 2-3 sentence plain-language explanation for a bench
immunologist. The hard grounding rule (brief §7.3): interpret ONLY the numbers in the record;
never invent a p-value, effect size, or figure; if a value is absent, say it is untested.

Cached by (gene, cytokine, condition) → explanations_cache.json. The app serves the cache; the
deterministic template (frontend whyText()) is the always-available fallback, so the live demo
NEVER depends on an API call. We only cache the genes a user actually inspects (the example chips
+ a sample per verdict), not all 34k records — the cache is a demo asset, not a bulk job.

Model: Haiku 4.5 — a cheap, high-volume, structured interpretation task. No tools, single turn.

Run:  python pipeline/04_explanations.py            # default gene set
      python pipeline/04_explanations.py --genes ITK TSC1 LCP2
If no Anthropic credentials are configured, this exits cleanly without writing (the template
fallback covers every gene), so a keyless clone still builds.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
if _APP not in sys.path:
    sys.path.insert(0, _APP)

import pandas as pd  # noqa: E402

from sera.core.explanation import (  # noqa: E402 — the ONE grounded-prompt source
    MODEL, SYSTEM_PROMPT, build_record, user_prompt)

_CONC = os.path.join(_APP, "sera", "data", "artifacts", "concordance.parquet")
_OUT = os.path.join(_APP, "sera", "data", "artifacts", "explanations_cache.json")

# The genes worth caching for the demo (chips + a couple of extra discordant/protein-only cases).
DEFAULT_GENES = ("ITK", "BCL10", "VAV1", "TSC1", "LCP2", "VPS37B", "ZNF250", "IL2RA")


def _has_credentials() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.path.exists(os.path.expanduser("~/.claude/.credentials.json")):
        return True
    import shutil
    return shutil.which("claude") is not None


async def _explain(record: dict) -> str:
    """One grounded explanation via a single-turn, no-tools Claude call."""
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock)

    options = ClaudeAgentOptions(system_prompt=SYSTEM_PROMPT, model=MODEL,
                                 max_turns=1, allowed_tools=[])
    text = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(user_prompt(record))
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text.append(block.text)
    return "".join(text).strip()


async def build(genes: list[str], progress=print) -> dict:
    df = pd.read_parquet(_CONC)
    cache: dict = {}
    for gene in genes:
        rows = df[df["gene"] == gene]
        if rows.empty:
            progress(f"  {gene}: not in concordance table — skipped")
            continue
        for row in rows.to_dict("records"):
            # normalise numpy/NaN to plain JSON
            clean = {k: (None if (isinstance(v, float) and pd.isna(v))
                         else (v.item() if hasattr(v, "item") else v))
                     for k, v in row.items()}
            rec = build_record(clean, gene)
            key = f"{gene}|{rec['cytokine']}|{rec['condition']}"
            try:
                cache[key] = {"explanation": await _explain(rec), "grounded_from": rec}
                progress(f"  {key}: ok")
            except Exception as e:  # noqa: BLE001 — a failed gene falls back to the template
                progress(f"  {key}: FAILED ({e}) — template fallback will cover it")
    return cache


def main() -> int:
    ap = argparse.ArgumentParser(description="Precompute grounded Claude explanations.")
    ap.add_argument("--genes", nargs="*", default=list(DEFAULT_GENES))
    args = ap.parse_args()

    if not _has_credentials():
        print("[explanations] no Anthropic credentials — skipping (the deterministic template "
              "fallback covers every gene). Set ANTHROPIC_API_KEY or log in with the claude CLI "
              "to precompute explanations.")
        return 0

    cache = asyncio.run(build(args.genes))
    if not cache:
        print("[explanations] nothing generated.")
        return 0
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    with open(_OUT, "w") as fh:
        json.dump(cache, fh, indent=1)
    print(f"[write] {_OUT}  ({len(cache)} explanations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
