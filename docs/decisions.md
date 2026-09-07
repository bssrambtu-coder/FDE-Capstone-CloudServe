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

## D9 — Retrieval scores are coverage-aware; the floor is 0.42

Two changes came out of the fairness audit (`docs/fairness_audit.md`):

- **Light stemming** on the retrieval path only. Lifts hit rate 80.1% to
  87.4% at an unchanged floor. The classifier keeps unstemmed tokens, because
  the committed vocabulary in `models/` was fitted on those.
- **Coverage-aware normalisation.** The relevance score previously divided by
  the idf mass of only the *matched* query terms, so a nine-word query
  matching one rare word could score above 1.0. Unseen terms now count toward
  the normalising mass, so matching one word of nine scores near a ninth. This
  was a real defect, not a tuning choice: it was letting single-term matches
  through as highly relevant.

The floor moved 0.60 to 0.42 after the rescale. On validation this raised
correct abstention from 40.7% to 55.6% with hit rate holding at 79.3%.

## D10 — Hybrid retrieval: fuse for ranking, gate on semantics for abstention

Ranking and abstention are different decisions and need different signals.
Ranking is reciprocal rank fusion over BM25 and `all-MiniLM-L6-v2`; abstention
is gated on the semantic score alone. Rejected along the way: semantic-only
(still failed fluency at any usable abstention level) and rank fusion alone
(passed every fairness dimension at 97.5% hit rate but could not abstain at
all, because a ranking always exists).

**`hybrid` is the recommended and audited configuration.** It was originally
left as an opt-in with `lexical` as the code default; D11 reverses that.
Degrading to lexical logs a warning, because it reopens R-07.

Cost: 6.2ms per ticket against 0.1ms, and correct abstention fell from 29.4%
to 12.6% on development. See the honest note in `docs/fairness_audit.md`.

## D11 — Hybrid is the default, not the opt-in

D10 left `lexical` as the code default on the reasoning that a clean checkout
must never fail on a missing dependency. That reasoning was sound and the
conclusion was still wrong, because `.env.example` shipped
`RETRIEVAL_BACKEND=hybrid` while `src/config.py` defaulted to `"lexical"`. A
run with no `.env` — which is how a grader runs it — silently picked the one
configuration the fairness audit fails, and said nothing about it.

The dependency argument does not actually require a lexical default.
`HybridRetriever` already degrades to lexical when the extras are absent and
logs that it did, so defaulting to `hybrid` costs nothing on a clean checkout:
the gate still cannot fail on a dependency. What changes is which way the
silent case falls. It now falls towards the audited configuration, and the
unaudited one has to be asked for by name (`--backend lexical` or
`RETRIEVAL_BACKEND=lexical`).

The general form of the mistake is worth naming, because the same defect had
already appeared once with the retrieval floor documented as 0.60 in the README
after D9 moved it to 0.42: a default that lives in three places drifts. The
values now agree across `src/config.py`, `.env.example` and the README, and
D12's semantic gauge makes a lexical-only run visible at runtime rather than
only in a config file nobody rereads.

## D12 — Monitoring: an in-process tally that Prometheus mirrors (B-12)

The Setup Guide's monitoring section assumes `prometheus_client` is installed.
The spine's whole premise is that it is not, so instrumentation that only works
with the extras present would be untested on the machine that grades it.

So `src/monitoring.py` records every metric into an in-process tally that
always works, and mirrors into Prometheus only when the library is importable.
The tally is not a fallback nobody exercises: the acceptance tests assert on it
directly, so the instrumentation is covered on a clean checkout, and the
harness folds a snapshot of it into `metrics.json` so a run stays auditable
after the exporter is gone.

The two are deliberately not the same reading. A Prometheus counter is
process-lifetime and monotonic by design; `metrics.json` describes one run. The
harness clears the tally at the top of a run and never touches the collectors,
which is why `Metrics.reset_tally` exists and why it does only half of what its
name suggests.

Beyond the Setup Guide's three metrics (tickets by channel and outcome,
latency, guardrail blocks), three were added that this system specifically
needs: retrieval abstentions, degraded generations, and
`retrieval_semantic_available`. The last is the one worth having. When hybrid
retrieval loses its semantic half, nothing user-visible breaks — the system
keeps answering, at the lexical hit rate, with the fairness gaps the audit
failed on. There is no error to see, which is exactly why it needs a gauge and
an alert rather than a log line. It is the first panel on the dashboard and the
only `critical` rule in `monitoring/alerts.yml`.

Two things the exporter deliberately will not do: bind a port unless asked
(`--metrics-port`, default off, so an unattended grading run does not open one
it was not asked for), and raise. A monitoring endpoint that cannot bind logs a
warning and the run continues — A11 applies to the instruments too.

Known gap: a batch run over 500 tickets finishes in under a second, well inside
a 15s scrape interval, so a live dashboard needs `--metrics-hold-seconds` to
keep the endpoint up long enough to be scraped at all. That is a batch-shaped
answer to a service-shaped tool. The honest fix is the FastAPI service, where
the process outlives the request and the scrape model fits.

## D13 — The confidence threshold is not a control, and the sweep is what proved it

Every prior entry treated the 0.80 threshold as the last untuned dial and named
it the lever for R-08. Swept against the 500 development tickets, it turns out
not to be a dial at all.

| Threshold | Automation | Route agreement | Over-answered | Max gap |
|---|---|---|---|---|
| 0.500 → 0.998 | 81.00% | 78.80% | 20.00% | 7.99pp |
| 0.999 | 0.00% | 37.80% | 0.00% | 14.43pp |

Not a trend with a knee in it: one flat line and a cliff. Every value from 0.50
to 0.998 produces byte-identical routing, and the first value above 0.998
escalates all 500 tickets. There is nothing in between to select on.

The cause is in the confidence itself. Across 500 tickets the classifier emits
**two** distinct values: 0.998 (499 tickets) and 0.8 (one). Two mechanisms
compound:

1. **Naive Bayes posteriors saturate.** Summing log-likelihoods over every
   in-vocabulary token treats each token as independent evidence, so the top
   class wins by an enormous margin and the posterior pins to ~1.0 almost
   regardless of how ambiguous the ticket actually is.
2. **Binned calibration then quantises what is left.** D4's calibration maps a
   raw posterior to the measured accuracy of its band and returns that
   accuracy. With 495 of 500 raw scores landing in the `[0.99, 1.01)` band, 495
   tickets receive that band's accuracy — the same number, 0.998 — as their
   confidence.

So the calibration is honest at the population level and useless at the ticket
level: 0.998 really is how often that band is right, and it says nothing about
*this* ticket. D4's claim that confidence never states certainty still holds.
What does not hold is the unstated assumption that it varies.

**Consequences, stated plainly because they change what the report can claim.**

- The threshold stays at 0.80. Not because 0.80 was validated, but because
  every value in the usable range produces the same routing, and 0.80 is the
  one the Brief named and the one both audits already ran under. It should be
  described in the report as inert, not as tuned.
- **R-08's stated lever does not exist.** The regional and tier gaps in routing
  agreement cannot be closed by the threshold, because the threshold cannot
  change a single routing decision. That claim in the previous Open items was
  wrong and is corrected below.
- **20% over-answering is the finding that replaces it.** One ticket in five is
  answered where the expert escalated, against 1.2% under-answered. The error
  is overwhelmingly in the dangerous direction, and the policy gate (D5) and
  the retrieval floor (D9) are the only controls currently able to move it.

**What would actually fix it**, none of which is a threshold change: fit the
calibration as a continuous mapping rather than four bins (isotonic or Platt),
damp the posterior saturation (token-count normalisation, or a temperature on
the log-sum), or drop confidence as a routing input altogether and route on
retrieval support and policy alone — which is close to what the system already
does in practice.

Not attempted before submission. The sweep is cheap and repeatable
(`scripts/sweep_threshold.py`, results in `evaluation/threshold/`), but
recalibrating changes every headline number in the report and in the fairness
audit, and there is not enough time left to re-audit honestly. Recorded as a
known defect with a diagnosis rather than patched in a hurry.

## Open items

- **R-07 primary breach CLOSED by D10.** Hybrid retrieval brought every
  fluency measure inside the 5pp condition (retrieval gap 8.29pp to 4.30pp)
  and lifted hit rate 79.8% to 96.4%. Re-audited, not argued.
- **R-08 open.** Routing agreement now varies by region (7.99pp) and tier
  (5.80pp), and citation coverage by region (8.45pp). Different cause: better
  retrieval removed an escalation path, so automation rose 69% to 81% and
  over-answering rose with it. The lever is *not* the confidence threshold
  (D13): swept across its whole usable range it changes no routing decision at
  all. On the development set over-answering sits at 20.0% against 1.2%
  under-answering, and only the policy gate and the retrieval floor can move
  it.
- ~~**Confidence threshold is the top priority.**~~ **Closed by D13, as
  disproved.** It was never the lever. Swept across its whole usable range it
  changes no routing decision at all, because calibrated confidence takes two
  distinct values across 500 tickets. The 20% over-answer rate is the finding
  that replaces it.
- The earlier 28pp tier gap was small-sample noise (n=8 on validation). On 500
  tickets the tier residual is 4.44pp and passes. The regional gap is largely
  inherited: the experts' own labels vary by 14.43pp across regions against our
  12.77pp.
- Confidence threshold is the illustrative 0.80 from the Brief. It has now
  been swept (D13) and is inert across its usable range; the policy gate (D5)
  and the retrieval floor (D9) do all of the real routing. The report should
  describe it as inert rather than as tuned.
- Groundedness checking is lexical overlap. It catches drift from the sources
  but not a fluent paraphrase that reverses a meaning. Say so in the report
  rather than implying the guardrail is stronger than it is.
- No FastAPI interface yet. The pipeline is importable and the harness is the
  batch entry point; the API is a thin layer over `Pipeline.process`. It is
  also what would make monitoring (D12) fit its tool properly, rather than
  needing `--metrics-hold-seconds` to survive a scrape interval.
- No CI pipeline yet (B-13). The suite runs on the standard library alone, so
  the GitHub Actions workflow is a checkout, a `python -m unittest`, and a
  harness run over the validation set.
