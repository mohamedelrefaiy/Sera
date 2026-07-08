"""Serve the Target Triage web app.

    python -m target_triage.api.serve            # http://127.0.0.1:8000
    python -m target_triage.api.serve --port 9000

The deterministic shortlist works with no API key; the chat panel needs one.
"""
from __future__ import annotations

import argparse
import os
import sys

# Make the package importable even when launched from an arbitrary cwd (e.g. the
# preview harness) without relying on an editable install being on the path.
# serve.py lives at src/target_triage/api/serve.py — three levels up reaches src/.
_SRC = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Target Triage web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"Target Triage → http://{args.host}:{args.port}")
    uvicorn.run("target_triage.api:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
