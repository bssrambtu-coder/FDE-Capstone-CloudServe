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
| `--threshold` | `0.80` | confidence below which a ticket escalates |
| `--retrieval-floor` | `0.42` | relevance below which retrieval returns nothing |
| `--backend` | `lexical` | `lexical` (BM25, no deps) or `chroma` |
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

`lexical` is the default because it needs nothing installed, so the gate can
never fail on a dependency. **`hybrid` is the recommended configuration**: it
is the one the fairness condition holds under, and it is what the audit in
`docs/fairness_audit.md` signs off.

```bash
pip install -r requirements.txt
python3 -m evaluation.harness --input <file> --output <dir> --backend hybrid
```

| Backend | Retrieval hit rate | Fluency gap | Regional gap | Needs |
|---|---|---|---|---|
| `lexical` | 79.8% | 8.3pp FAIL | 15.0pp FAIL | nothing |
| **`hybrid`** | **96.4%** | **4.3pp ok** | **4.9pp ok** | extras |

Measured on the 500 development tickets. `hybrid` ranks by reciprocal rank
fusion over BM25 and `all-MiniLM-L6-v2`, and gates abstention on the semantic
score alone. If the extras are missing it degrades to lexical and logs a
warning, because degrading reopens the fairness gap.

## Attribution

Written by Shashidhar B S. Developed with Claude Code (Anthropic); see the AI
tool declaration in the project report for what it contributed and where its
output was corrected. No third-party implementation was copied. The datasets in
`Capstone_Pack/` are supplied course material.
