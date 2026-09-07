# CloudServe Solutions — support triage system

Reduces the share of CloudServe support tickets that need a human by answering
the ones its own documentation already answers, and escalating everything else
with the draft, the sources and the reason attached.

Built for the Forward Deployed AI Engineering capstone. The client asked for a
chatbot; this is not one. See `docs/decisions.md` for why.

## Quick start

No install step. Python 3.10 or later, standard library only.

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

Swept across 0.50 to 0.999 on the 500 development tickets, the threshold does
not change a single routing decision until 0.999, at which point every ticket
escalates. The classifier emits two distinct confidence values across the whole
set — 0.998 for 499 tickets and 0.8 for one — because Naive Bayes posteriors
saturate and D4's four-band calibration then quantises them to a band accuracy.

It is left at 0.80 and described as inert rather than tuned. The real finding
the sweep surfaced is that 20.0% of tickets are answered where the expert
escalated, against 1.2% the other way. Full reasoning and the fixes that would
actually work are in `docs/decisions.md` D13.

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

Written by Shashidhar B S. Developed with Claude Code (Anthropic); see the AI
tool declaration in the project report for what it contributed and where its
output was corrected. No third-party implementation was copied. The datasets in
`Capstone_Pack/` are supplied course material.
