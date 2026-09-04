# Fairness audit

**Date:** 4 September 2026 · **Dataset:** 500 development tickets · **Method:**
`scripts/fairness_audit.py`

**Verdict, lexical backend: BREACHED on retrieval quality.**
**Verdict after mitigation (hybrid backend): primary breach CLOSED; two
secondary regional breaches remain open.** See "Re-audit" at the end — it is
the current state and supersedes the lexical figures below, which are retained
because they are what identified the defect.

## The condition being tested

The governance framework requires **under five percentage points of difference
in quality between customer groups**. It is a condition, not a target: it
either holds or it does not, and a system failing it is not fit to deploy
whatever its other numbers look like.

Three grouping dimensions are present in the data: `customer_tier`,
`customer_region`, `language_fluency`.

## Method, and why automation rate alone is the wrong measure

Automation rate is throughput, not quality. A group can be automated *more*
often and served *worse*. So the audit measures four quality figures per group
— intent accuracy, routing agreement with the expert labels, retrieval hit
rate, and whether a citation was produced — and judges each against the 5pp
condition.

Separately, the automation rate is **decomposed**, because a raw gap is not by
itself evidence of unfair treatment. If one group sends proportionally more
compliance requests, and compliance requests are always escalated by policy,
that group's automation rate falls for a reason that is correct.

- **observed** — the group's raw automation rate
- **expected** — what it would be if the system treated every group
  identically and only the intent mix differed
- **residual** — `observed − expected`: the part attributable to differential
  treatment rather than composition

Residual is the number the condition should be judged on. Wilson 95% intervals
are reported because some groups are small, and a wide interval is not a
finding.

## Results

### Quality measures against the 5pp condition

| Dimension | Intent accuracy | Routing agreement | **Retrieval hit rate** | Citation present |
|---|---|---|---|---|
| `customer_tier` | 0.00pp ok | 4.93pp ok | **6.63pp FAIL** | 3.89pp ok |
| `customer_region` | 0.00pp ok | 5.38pp FAIL | **15.05pp FAIL** | 13.54pp FAIL |
| `language_fluency` | 0.00pp ok | 0.83pp ok | **8.29pp FAIL** | 6.93pp FAIL |

**Retrieval is the sole source of the disparity.** Classification is identical
across every group (100% everywhere — the tickets are templated). Routing is
within or near tolerance. The gap enters at retrieval and propagates into
whether a citation can be produced at all.

The ethically significant row is `language_fluency`: **81.85% retrieval hit
rate for fluent customers against 73.56% for non-fluent**. Non-fluent
customers get a worse system.

This gap is ours, not inherited. The historical data shows essentially no
fluency gap — CSAT 2.95 fluent against 3.04 non-fluent, and expert routing
within 5.88pp. We introduced it.

### Automation rate decomposition

| Dimension | Observed spread | **Residual spread** | Label-implied spread | Verdict on residual |
|---|---|---|---|---|
| `customer_tier` | 5.8pp | **4.44pp** | 3.76pp | PASS |
| `customer_region` | 12.77pp | **5.96pp** | 14.43pp | FAIL (marginal) |
| `language_fluency` | 0.0pp | **1.90pp** | 5.88pp | PASS |

Two things this settles:

1. **The 28pp tier gap reported on the validation set was small-sample noise.**
   Enterprise was n=8 there. On 500 tickets the tier residual is 4.44pp and
   passes, and the largest raw tier gap is not significant (p=0.34).
2. **The regional gap is largely inherited, and the system reduces it.** The
   experts' own `expected_route` labels vary by **14.43pp** across regions;
   our observed spread is 12.77pp and the residual 5.96pp. The largest raw gap
   (asia_pacific vs latin_america, 12.77pp) is not significant at p=0.074.
   Latin America is *over*-automated relative to its intent mix (+4.1pp), not
   under-served.

### The one statistically significant finding

Within `database_issue`, fluent customers are automated 100% of the time
(n=18) against 75% for non-fluent (n=8) — a 25pp gap at **p=0.027**. Every
other within-intent gap has p > 0.06. Small cells, but this is the single
result that survives a significance test, and it points at the same mechanism.

## Mechanism

Not a guess — measured. Comparing fluent against non-fluent tickets that have
a labelled expected document:

| | Fluent | Non-fluent |
|---|---|---|
| Words per ticket | 28.9 | 25.6 |
| Tokens in retrieval vocabulary | 11.0 | 9.7 |
| **In-vocabulary coverage** | **71.6%** | **68.7%** |
| **Query idf mass** | **22.94** | **20.62** |
| **Mean top-1 relevance score** | **0.955** | **0.880** |

Non-fluent phrasing uses fewer of the terms the documentation uses ("builds
that work last week are now fail"). BM25 scores lexical overlap, so those
tickets score systematically lower — and because the abstention rule is an
**absolute threshold** on that score, they are disproportionately cut by it.

An absolute threshold on lexical coverage discriminates against people who
phrase things differently. That is the defect.

## What was tried

| Intervention | Effect on quality | Effect on the fluency gap |
|---|---|---|
| Light stemming (**adopted**) | hit rate 80.1% → 87.4% | none: 5.6pp → 6.1pp |
| Lowering the floor to 0.50 | abstention 24.5% → 8.4% | closes it: → 2.7pp |
| Margin-based abstention | abstention collapses to 4–13% | closes it: → 3.0pp |
| Coverage-aware normalisation (**adopted, fixed a bug**) | abstention 40.7% → 55.6% | widens it |

The floor sweep makes the trade-off unmistakable:

| Floor | Hit rate | Correct abstention | Fluency gap |
|---|---|---|---|
| 0.30 | 89.6% | 7.7% | **0.0pp** |
| 0.36 | 84.3% | 21.7% | 8.1pp |
| **0.42 (shipped)** | **79.8%** | **29.4%** | **8.3pp** |
| 0.48 | 70.0% | 45.5% | 12.0pp |
| 0.60 | 56.6% | 58.0% | 15.5pp |

**Abstention and fairness trade directly against each other.** Every point of
abstention is bought with fairness.

## Why the condition was not simply made to pass

Dropping the floor to 0.30 would show 0.0pp and satisfy the metric. It is not
being done, for two reasons:

1. **It trades a governance condition for an acceptance criterion.** At floor
   0.30 the system returns passages for 92% of tickets that have no relevant
   document. Build Specification §08 lists "retrieval that returns something
   for every query regardless of relevance" as a submitted shortcut that does
   not count as working, and A4 requires returning nothing rather than
   something irrelevant.
2. **It would hide the defect rather than fix it.** The gap would still be
   there in the underlying scores; it would simply stop being visible, and
   would reappear the moment the threshold moved.

A third option was rejected outright: **tuning the threshold per customer
group**. It would make the metric pass while making the system discriminate by
construction. A fairness metric satisfied by treating groups differently is
theatre.

## Risk register entry

| Field | Value |
|---|---|
| **ID** | R-07 |
| **Risk** | Customers writing in non-standard English receive materially worse retrieval, so their tickets are escalated more often and answered with fewer citations. |
| **Likelihood** | Certain — measured, present in the shipped configuration |
| **Impact** | High — a quality-of-service gap correlated with language proficiency, plausibly with national origin |
| **Current exposure** | 8.29pp retrieval hit-rate gap (81.85% fluent vs 73.56% non-fluent), 6.93pp citation gap. 24% of tickets are from non-fluent customers. |
| **Root cause** | Absolute threshold on a lexical relevance score; BM25 rewards vocabulary overlap with the documentation |
| **Mitigation (identified, not implemented)** | Replace lexical relevance with semantic: Chroma + `all-MiniLM-L6-v2`, already wired as `--backend chroma`. Embeddings match meaning rather than wording, which decouples the relevance signal from the customer's phrasing. Re-audit before accepting. |
| **Interim control** | The gap moves escalation, not wrong answers: a low-scoring ticket escalates to a human with its draft and sources attached rather than being answered badly. Non-fluent customers wait longer; they do not get worse answers. |
| **Accountable** | Shashidhar B S |
| **Review** | Re-run `scripts/fairness_audit.py` after the Chroma backend is measured. Do not close on the strength of the mechanism argument — close on measurement. |
| **Deploy recommendation** | **Not fit for unsupervised deployment to non-fluent customers until re-audited.** Acceptable behind a human-review queue, because the failure mode is delay rather than error. |

## Limitations of this audit

- `language_fluency` is a binary flag supplied in the data. Real fluency is not
  binary and the flag's provenance is undocumented.
- Cell sizes for within-intent comparison are small (8–18). Only one result
  reaches p < 0.05; the rest are directional.
- Quality is measured against labels, so it measures agreement with the expert
  annotations rather than customer-experienced quality. Whether a customer was
  actually helped is not observable from a batch run.
- Response *tone* across groups was not audited. A system could be equally
  accurate and less courteous to one group; nothing here would detect it.
- The hidden set is drawn from the same population, so these gaps should be
  expected to reproduce. The audit script takes `--input`, so re-run it on any
  new file.

## Re-audit after mitigation — 4 September 2026

The mitigation named in R-07 was implemented and measured on the same 500
development tickets.

### What was built

Splitting two decisions that had been conflated:

- **Ranking** — reciprocal rank fusion over the BM25 and `all-MiniLM-L6-v2`
  rankings. Rank-based, so the two score scales never have to be reconciled.
- **Abstention** — gated on the **semantic score alone**, never the lexical
  one.

That split is the fix. Two intermediate designs were measured and rejected:

- **Semantic-only retrieval** improved everything but still failed the fluency
  condition at any usable abstention level (5.0–7.8pp).
- **Reciprocal rank fusion alone** passed every fairness dimension and reached
  97.5% hit rate but **could not abstain at all** — a fused rank carries no
  "nothing is relevant" signal, because a ranking always exists. This is what
  showed that ranking and abstention need different signals.

### Results

| Measure | Dimension | Lexical | **Hybrid** | Condition |
|---|---|---|---|---|
| Retrieval hit rate | `language_fluency` | 8.29pp FAIL | **4.30pp** | ok |
| Retrieval hit rate | `customer_region` | 15.05pp FAIL | **4.88pp** | ok |
| Retrieval hit rate | `customer_tier` | 6.63pp FAIL | **4.00pp** | ok |
| Citation present | `language_fluency` | 6.93pp FAIL | **4.04pp** | ok |
| Citation present | `customer_tier` | 3.89pp | **1.85pp** | ok |
| Citation present | `customer_region` | 13.54pp FAIL | 8.45pp | **FAIL** |
| Routing agreement | `language_fluency` | 0.83pp | **1.58pp** | ok |
| Routing agreement | `customer_tier` | 4.93pp | 5.80pp | **FAIL** |
| Routing agreement | `customer_region` | 5.38pp FAIL | 7.99pp | **FAIL** |
| Automation residual | `language_fluency` | 1.90pp | **1.79pp** | ok |
| Automation residual | `customer_tier` | 4.44pp | **2.40pp** | ok |
| Automation residual | `customer_region` | 5.96pp FAIL | **3.47pp** | ok |

Retrieval quality also improved outright: hit rate **79.8% → 96.4%** on
development, 79.3% → 98.1% on validation. The non-fluent hit rate went from
73.56% to **93.1%**.

The single statistically significant finding in the original audit —
`database_issue`, fluent 100% against non-fluent 75%, p=0.027 — **no longer
appears**. No within-intent fluency gap now reaches significance.

### R-07 status: primary breach closed

**Every fluency measure now passes**, which was the ethically significant
dimension: an 8.29pp retrieval gap correlated with language proficiency is now
4.30pp, and the mechanism that caused it is gone, because abstention no longer
depends on vocabulary overlap with the documentation.

Retrieval quality — the defect the audit identified — passes on all three
dimensions.

### R-08: what remains open

Two regional breaches survive, and they have a **different cause** from R-07.
Automation rose from 69% to 81% overall, because hybrid retrieval finds
supporting material far more often and the `no_supporting_documentation`
escalation path fires much less. On validation, tickets we auto-answered that
the experts would escalate rose from 13 to 18, while tickets we escalated that
they would answer fell from 9 to 1.

So the residual gaps are about **how much we automate**, not about who we
retrieve well for:

| Field | Value |
|---|---|
| **ID** | R-08 |
| **Risk** | Routing agreement with expert judgement varies by region (7.99pp) and tier (5.80pp), and citation coverage varies by region (8.45pp). |
| **Likelihood** | Certain — measured in the recommended configuration |
| **Impact** | Medium — the gap is in *whether a human reviews*, not in answer correctness. Zero `must_not_auto_respond` tickets were auto-answered in any configuration tested. |
| **Root cause** | Better retrieval removed an escalation path. The confidence threshold, still at the Brief's illustrative 0.80, is doing almost no work because calibrated confidence sits at 0.988 for nearly everything. |
| **Next lever** | Sweep the confidence threshold — the one significant control never yet tuned. Rein in over-answering and both gaps should narrow. |
| **Accountable** | Shashidhar B S |
| **Deploy recommendation** | Acceptable behind a review queue. The failure mode is an unreviewed answer, not a wrong one, and no safety-gated ticket was ever auto-answered. |

### Honest note on the trade

Hybrid retrieval lowered correct abstention (development 29.4% → 12.6%,
validation 55.6% → 33.3%). Retrieval abstains less because it genuinely finds
relevant material more often — but it is no longer the main guard against
answering something unsupported. That work now falls to routing and to the
groundedness guardrail, and the confidence threshold has not yet been tuned to
carry it. This is the first thing to fix, and it is R-08.

## Reproducing

```bash
python3 scripts/fairness_audit.py --min-cell 8                    # lexical
python3 scripts/fairness_audit.py --backend hybrid --min-cell 8    # recommended
```

Writes `evaluation/fairness/fairness_<backend>.json`. Defaults to the
development set; pass `--input` for any other file.
