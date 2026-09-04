# Decision log (engineering)

Design decisions taken during the build, with the evidence behind each. This
feeds the PRD traceability chain and the report; the per-ticket decision log is
separate and lives in `storage/decisions.db`.

## D1 — Not a chatbot

The client asked for a chatbot. A chatbot is a delivery mechanism; it says
nothing about where an answer comes from, whether it is correct, or what
happens when it is unknown. What CloudServe needs is that fewer tickets require
a human and that the ones which do arrive with context attached. The system is
therefore a triage and drafting pipeline, not a conversational interface.

**Evidence:** 71.4% of the 500 development tickets are labelled
`answerable_from_docs: true`. Customers are waiting 8–12 hours for answers that
already exist in the corpus.

## D2 — Retrieval relevance floor of 0.60

Swept on the development set only. BM25 scores are normalised by the query's
total idf mass so one floor is comparable across queries of different lengths.

| Floor | Hit rate | Correct abstention | Balanced |
|---|---|---|---|
| 0.40 | 91.3% | 25.2% | 58.2% |
| **0.60** | **80.1%** | **41.3%** | **60.7%** |
| 0.70 | 67.2% | 55.2% | 61.2% |
| 0.80 | 57.4% | 64.3% | 60.9% |

0.70 is marginally better balanced, but 0.60 keeps 13 points more hit rate for
14 points of abstention, and a wrong retrieval is caught downstream by the
groundedness guardrail while a missed one escalates safely. Revisit in Stage 5.

**Known weakness:** abstention is the weakest part of the system. A score floor
alone caps at 57.7% balanced accuracy because the tickets that should abstain
are semantically rather than lexically distant — `feature_request` about the
dashboard looks like a dashboard document. Adding an intent gate lifts this to
61.2%. Semantic embeddings should do better here, which is the strongest
argument for adopting Chroma (D6).

## D3 — Urgency is an intent-conditioned prior, not a text model

Naive Bayes over ticket text scores 46.4% on urgency against a 45.2% majority
baseline: urgency is not recoverable from the wording in this dataset.
Conditioning on intent reaches 55.2%, so that is what ships.

Only two intents carry a real signal — `security_incident` is 92% high and
`rollback_request` 82% high. This is a finding about the data rather than a
modelling shortcut and belongs in the evaluation write-up.

## D4 — Confidence is calibrated, and never states certainty

Intent classification is 99.8% accurate under 5-fold cross-validation against a
5.8% majority baseline, because the tickets are templated and intent is
essentially given away lexically. The classifier is not the achievement here
and the report should not present it as one.

Raw Naive Bayes posteriors are unusable as confidence: 1.000 when correct,
0.514 when wrong. Confidence is therefore mapped onto the accuracy observed
out-of-fold for its band, and the bands are Laplace-smoothed so a perfect bin
reports 0.988 rather than 1.000. The governance framework requires stated
confidence within five points of observed accuracy; a system that claims
certainty has failed that condition regardless of its accuracy.

**Caveat:** with 495 of 500 predictions in one band, the calibration table is
degenerate. It will need refitting the moment an LLM classifier replaces Naive
Bayes, or if the hidden set is less templated than the development set.

## D5 — Four intents never auto-respond, whatever the confidence

`compliance_request`, `security_incident`, `feature_request` and
`unclear_request` are labelled for escalation in 100% of their 87 development
occurrences. These commit CloudServe to something the system is not entitled to
promise, so they are gated by policy ahead of the confidence check.

**Result:** 32.5% escalation on the validation set against a 30% target, with
**zero** tickets auto-answered that carry `must_not_auto_respond: true`. Routing
agrees with the expert labels on 70% of tickets.

Note the target itself is not reachable while being label-faithful: the labels'
own `expected_route` implies 37.8% escalation on development and 40.0% on
validation. The residual is compliance and security work that *should* reach a
human. The report should reframe the win as time-to-first-reply and escalation
quality rather than chase 30%.

## D6 — Chroma is adopted; LangGraph is not

The Project Brief names LangChain with LangGraph and Chroma in its "what to
use" table, but no acceptance criterion names a library, §04 explicitly invites
justified departure, and §09's own heading concedes "rules that apply
regardless of your technical choices".

- **Chroma + all-MiniLM-L6-v2:** adopted as `--backend chroma`, with BM25 as
  the fallback when the dependency or model is unavailable. Semantic similarity
  should also improve abstention (D2).
- **LangGraph:** not adopted. A5 requires the same input to produce the same
  routing decision and A8 requires logged decisions to reconcile exactly
  against tickets processed. Both are materially easier to guarantee in plain
  Python than through a pre-1.0 graph framework, and the six components are a
  fixed sequence rather than a graph needing dynamic traversal.

## D7 — The supplied dependency pins do not resolve

`06_Configuration/requirements.txt` pins `chromadb==0.3.21` alongside
`pydantic==2.0.0`. Chroma 0.3.x is a pydantic v1 release, so that set cannot
install together. `sentence-transformers==2.2.2` and `numpy==1.24.0` also fail
to build on Python 3.13+.

The spine therefore depends on the standard library alone and clears the gate
with no install step; `requirements.txt` carries only the optional extras, with
ranges rather than exact pins. Since the grader runs *this* README on a clean
machine, resolving the pin set is our responsibility rather than the pack's.

## D8 — Baseline figures are recomputed, not quoted

The Project Brief's baselines do not match the data. Computed from the
`history` field of the 500 development tickets:

| Metric | Brief says | Actually |
|---|---|---|
| First contact resolution | 42% | 43.8% |
| Escalation rate | 58% | 56.2% |
| CSAT | 3.2 / 5 | **2.97** |
| Repeat contacts | "not measured" | **21.6%** — it is measured |
| Median resolution | 8–12h | 214 min (3.6h) |

The computed figures are used throughout. CSAT and repeat contacts cannot be
measured from an unattended batch run at all, so the metrics report returns
null with a stated reason rather than a plausible number.

## Open items

- Fairness: automation rate spread is **28pp by customer tier** and **17pp by
  region** against a 5pp condition. Both exceed tolerance. Needs investigation
  before this could go live — the likely cause is intent mix differing by
  group rather than differential treatment, but that must be shown, not
  assumed. `language_fluency` shows a 1.2pp spread, so a gap appearing there
  later would be ours.
- Confidence threshold is still the illustrative 0.80 from the Brief. It has
  not been swept. With calibrated confidence at 0.988 for almost everything,
  the threshold currently does very little work; the policy gate (D5) is doing
  the real routing.
- Groundedness checking is lexical overlap. It catches drift from the sources
  but not a fluent paraphrase that reverses a meaning. Say so in the report
  rather than implying the guardrail is stronger than it is.
- No FastAPI interface yet. The pipeline is importable and the harness is the
  batch entry point; the API is a thin layer over `Pipeline.process`.
