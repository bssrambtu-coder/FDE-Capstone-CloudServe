# CloudServe Solutions — support triage system

Reduces the share of CloudServe support tickets that need a human by answering
the ones its own documentation already answers, and escalating everything else
with the draft, the sources and the reason attached.

Built for the Forward Deployed AI Engineering capstone. The client asked for a
chatbot; this is not one. See `docs/decisions.md` for why.

## Quick start

### OpenRouter connection

Copy `.env.example` to `.env` if `.env` does not already exist, then set
`OPENROUTER_API_KEY` locally. The application loads it automatically; Git
ignores this file. Never include credentials in reports or source control.
The example names the fixed free DeepSeek model used for final evaluation.
Check OpenRouter availability before a future rerun because free model catalogues change.
See https://openrouter.ai/docs/api/reference/overview for the API contract.

Use `--use-provider` to enable real OpenRouter calls. Without that flag the
pipeline uses local extractive generation. Missing credentials fail explicitly.

```bash
python -m evaluation.harness --input Capstone_Pack/05_Datasets/validation_tickets.json --output artifacts/openrouter-smoke --db artifacts/openrouter-smoke/decisions.db --backend lexical --use-provider --limit 3
```

Safety changes on 18 September 2026 block detected unsupported claims and
unresolvable inline citations, validate escalation drafts, and reject invalid
confidence values. Previously saved results below describe the earlier version
and must be remeasured. Lexical grounding remains a heuristic, not proof that
every claim is true. Routing calibration remains open; a local FastAPI interface
is now available as described below.

### Local API and operator pause control

From the project folder, on Windows:

```powershell
.\.venv\Scripts\python.exe -m src.api --backend lexical
```

Open http://127.0.0.1:8000/docs to try the API. Add `--use-provider` only when
ticket text and retrieved documentation are approved for transmission to
OpenRouter. Without it, processing stays local. `/health` reports the generation
mode, semantic retrieval availability and pause status. `/metrics` returns a
JSON snapshot, not the separate Prometheus export format.

`POST /tickets` accepts `ticket_id`, `channel`, `subject`, `body`, and optional
customer metadata. Channels are `email`, `chat`, `docs_comment`, and `forum`.
Unknown fields (including evaluation labels) are rejected. A minimal body is:

```json
{"ticket_id":"DEMO-1","channel":"chat","body":"How do I rotate an API key?"}
```

Replies separate `customer_response` from `agent_draft`; only an `answered`
decision carries a customer response. Every request has a separate audit run,
even when ticket IDs repeat. Database failures return 503 without releasing
the reply. This API prepares responses; it does not send messages to customers.

```powershell
.\.venv\Scripts\python.exe -m src.control disable
.\.venv\Scripts\python.exe -m src.control status
.\.venv\Scripts\python.exe -m src.control enable
```

The pause persists across restarts and applies to both API and batch processing.
Paused tickets are escalated and logged without calling the model. The pause
is checked again after generation; it cannot recall a reply already released.
There is deliberately no remote administration endpoint. Keep the server bound
to localhost: authentication, deployment hardening, retention controls and
outbound delivery are not implemented. Local decision logs contain ticket text.

Final verification on 19 September 2026: all 73 tests passed with semantic
retrieval available. The hybrid/extractive 80-ticket validation produced 56
automatic answers, 24 escalations, no blocked drafts and no pipeline errors,
with all 80 decisions reconciled. The approved full live OpenRouter run
produced 54 released answers, 24 escalations and two groundedness blocks, with
zero pipeline errors and 80 reconciled records. It made 66 provider calls,
including six cache hits and four safe fallbacks. No must-not-auto-respond
ticket was released. These are pipeline outcomes, not verified resolutions;
FCR, CSAT and repeat contact remain unmeasured.

Install the pinned environment before running the full system. The deliberate
standard-library CI lane separately verifies that the batch safety spine can
degrade without optional semantic, API or monitoring packages.

```bash
python3 -m unittest discover -s tests
```

```bash
python3 -m evaluation.harness \
  --input Capstone_Pack/05_Datasets/validation_tickets.json \
  --output evaluation/results
```

That writes `results.json`, `metrics.json` and `metrics.md` into the output
directory, and one row per ticket into `storage/decisions.db`.

## Running against a file you have not seen

The harness takes an input path and an output path as arguments and assumes
nothing about the ticket count, the filename, or whether the file carries
labels. It accepts a bare JSON array, an object wrapping one under
`tickets`/`data`/`items`, or JSONL.

```bash
python3 -m evaluation.harness --input /path/to/hidden_set.json --output /tmp/out
```

Exit codes: `0` success, `1` the decision log did not reconcile against the
tickets processed, `2` the input file was missing or empty.

## Options

| Flag | Default | What it does |
|---|---|---|
| `--input` | required | ticket file to process |
| `--output` | required | directory for results and metrics |
| `--corpus` | `Capstone_Pack/05_Datasets/documentation.json` | documentation corpus |
| `--threshold` | `0.80` | confidence below which a ticket escalates (inert — see D13) |
| `--retrieval-floor` | `0.42` | relevance below which retrieval returns nothing |
| `--backend` | `hybrid` | `hybrid` (BM25 + MiniLM, audited), `lexical` (BM25, no deps) or `chroma` |
| `--semantic-gate` | `0.60` | hybrid only: semantic score below which retrieval abstains |
| `--use-provider` | off | call the model provider; without it, generation is extractive |
| `--limit` | all | process only the first N tickets |
| `--db` | `storage/decisions.db` | decision log |

## Architecture

Six components in sequence, each swappable behind the types in `src/models.py`:

| Component | File | What it does |
|---|---|---|
| Ingest | `src/ingest.py` | four channels to one representation |
| Classify | `src/classify.py` | intent, urgency, calibrated confidence |
| Retrieve | `src/retrieve.py` | BM25 or Chroma, abstains below the floor |
| Route | `src/route.py` | deterministic: policy, then confidence, then support |
| Generate | `src/generate.py` | grounded draft with resolvable citations |
| Validate | `src/validate.py` | guardrails that block, not warn |

Cutting across them: `src/decision_log.py` (SQLite, one row per ticket),
`src/providers.py` (cache, backoff, circuit breaker, fault injection), and
`evaluation/metrics.py`.

The classifier weights in `models/` are fitted offline, so runtime needs
neither the training data nor a model download:

```bash
python3 scripts/train_classifier.py
```

## Inducing failure (A11)

```bash
FAULT_MODE=outage python3 -m evaluation.harness --input <file> --output /tmp/out --use-provider
```

`outage`, `timeout`, `ratelimit` and `malformed` are all handled: the system
falls back to extractive generation, marks the outcome degraded, and continues.
The circuit breaker stops calling a dead provider after five consecutive
failures.

## Retrieval backends

**`hybrid` is the default**, because it is the configuration the fairness
condition holds under and a run with no `.env` must not silently pick the one
the audit fails. Defaulting to it costs nothing: with the extras missing it
degrades to lexical and says so in the log, so the gate can still never fail on
a dependency. `docs/fairness_audit.md` signs off the hybrid numbers.

Force the zero-dependency path with `--backend lexical` or
`RETRIEVAL_BACKEND=lexical`.

```bash
pip install -r requirements.txt
python3 -m evaluation.harness --input <file> --output <dir> --backend hybrid
```

| Backend | Retrieval hit rate | Fluency gap | Regional gap | Needs |
|---|---|---|---|---|
| `lexical` | 79.8% | 8.3pp FAIL | 15.0pp FAIL | nothing |
| **`hybrid`** (default) | **96.4%** | **4.3pp ok** | **4.9pp ok** | extras |

Measured on the 500 development tickets. `hybrid` ranks by reciprocal rank
fusion over BM25 and `all-MiniLM-L6-v2`, and gates abstention on the semantic
score alone. If the extras are missing it degrades to lexical and logs a
warning, because degrading reopens the fairness gap.

## The confidence threshold is inert

```bash
python3 scripts/sweep_threshold.py --backend hybrid
```

After grouped temperature calibration, the development sweep remains flat from
0.50 through 0.99: 74.6% automation, 75.6% route agreement, 18.4% over-answering
and 6.0% under-answering. At 0.995 one more ticket escalates and agreement falls.
Confidence is still concentrated near 1.0, so the threshold remains a weak
control despite better held-out log loss and Brier score.

It is left at 0.80 and described as effectively inert rather than tuned. The
real finding is dangerous over-answering; full reasoning and the calibration
evidence are in `docs/decisions.md` and `evaluation/final/`.

## Monitoring

```bash
python3 -m evaluation.harness --input <file> --output <dir> \
    --metrics-port 8001 --metrics-hold-seconds 60
prometheus --config.file=monitoring/prometheus.yml
```

Then import `monitoring/grafana_dashboard.json` into Grafana against that
Prometheus datasource.

`prometheus_client` is optional in the same way Chroma is. `src/monitoring.py`
records into an in-process tally that always works and mirrors into Prometheus
only when the library is installed; either way the numbers land in the run's
`metrics.json` under `monitoring`. Without `--metrics-port` no port is opened,
so an unattended grading run behaves exactly as before.

| Metric | Type | What it answers |
|---|---|---|
| `tickets_processed_total{channel,outcome}` | counter | volume, and how much reaches a person |
| `response_seconds` | histogram | end to end, ingest to validated draft |
| `guardrail_blocks_total{guardrail}` | counter | which guardrail is doing the work |
| `escalations_total{rule}` | counter | why tickets reach a person |
| `retrieval_abstentions_total` | counter | how often retrieval returns nothing (A4) |
| `responses_degraded_total` | counter | provider failures that fell back to extractive |
| `retrieval_semantic_available` | gauge | **1 hybrid, 0 lexical-only** |

That gauge is the alarm worth having. When hybrid retrieval loses its semantic
half nothing user-visible breaks — the system keeps answering, at the lexical
hit rate, with the fairness gaps the audit failed on (R-07). It is the first
panel on the dashboard and the only `critical` rule in `monitoring/alerts.yml`.

The `--metrics-hold-seconds` flag exists because a run over 500 tickets
finishes in under a second, inside a 15s scrape interval. Without the hold the
process exits before Prometheus ever reaches it and the dashboard stays empty.

## Attribution

Written by Shashidhar B S. The initial implementation used Claude Opus; final
review, safety, calibration, evaluation and packaging used OpenAI Codex. The
approved live generator was the configured DeepSeek model through OpenRouter.
See `docs/ATTRIBUTION.md`. No third-party implementation was copied. The
datasets in `Capstone_Pack/` are supplied course material.
