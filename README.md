# Sera

Rank and adversarially verify druggable T-cell regulators from Perturb-seq — a
reusable instrument that runs the same pipeline on any screen. Built for the
*Built with Claude: Life Sciences* hackathon. New work, MIT-licensed.

Claude is not handed a finished shortlist. It ranks candidates, tries to **refute**
the ones worth trusting (donor/guide robustness, real-knockdown, power gates),
cross-checks them against Open Targets and ClinicalTrials.gov, and reasons over the
numbers to produce a mechanism-annotated, scrutiny-survived shortlist — showing its
work, including the candidates it rejects.

## Layout

The package and its build files sit at the repo root — a conventional, installable
Python app:

```
.
├── sera/       the package (app-layout: no src/ wrapper)
│   ├── core/            deterministic pipeline (data, ranking, verify, shortlist, controls)
│   ├── clients/         read-only external evidence (Open Targets, ClinicalTrials.gov)
│   ├── llm/             Claude-facing adapters (@tool wrappers, system prompt, SDK options)
│   ├── agent/           the driver loop that lets Claude orchestrate the tools
│   ├── api/             FastAPI web surface + uvicorn launcher
│   ├── frontend/        the static frontend the app serves (bundled with the package)
│   ├── data/            the screen CSVs the pipeline reads (bundled with the package)
│   └── eval/            the controls-first gate + unit tests
├── pyproject.toml
└── requirements.txt
```

Dependency direction reads top-to-bottom: `api → agent → llm → core → clients`.
`core/` knows nothing about Claude; `llm/` adapts it to the model.

## Quickstart

One command runs the whole app (it creates the venv and installs on first run):

```bash
./start.sh            # http://127.0.0.1:8010
PORT=9000 ./start.sh  # pick a port
```

`start.sh` launches a single server: the FastAPI backend serves both the JSON API
(`/api/*`) and the static frontend (`sera/frontend/`) on one port — there
is no separate frontend process. The deterministic shortlist works with no API key;
the chat panel needs `ANTHROPIC_API_KEY` (or a logged-in `claude` CLI).

Or run the pieces manually:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

# The web app (frontend + backend, single server)
python sera/api/serve.py --port 8010   # http://127.0.0.1:8010

# The deterministic pipeline's positive-control gate (no API key needed)
python -m sera.core.controls           # expect 5/5 PASS on Marson + Schmidt2022

# The live agent end-to-end (requires ANTHROPIC_API_KEY or a logged-in claude CLI)
python -m sera
```

## Tests

```bash
pytest sera/eval
```

The controls-first gate is the trust contract: before any novel pick is shown, the
tool must recover the **known** biology of the screen. An item isn't "done" until
its positive-control eval prints PASS.

## License

MIT — see [LICENSE](LICENSE).
