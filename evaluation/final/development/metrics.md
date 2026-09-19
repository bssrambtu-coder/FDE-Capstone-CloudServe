# Evaluation metrics

## Volume

- tickets processed: 500
- answered automatically: 373
- escalated: 127
- blocked by guardrails: 0
- pipeline errors: 0
- degraded responses: 0

## Business outcomes

- automation rate pct: 74.6
- first contact resolution pct: None
- escalation rate pct: 25.4
- mean response time ms: 21.19
- median response time ms: 21.79
- customer satisfaction: None
- repeat contacts: None

Resolution requires customer follow-up; automatic answers are not confirmed resolutions.

## Technical performance

- intent accuracy: 1.0
- intent macro precision: 1.0
- urgency accuracy: 0.552
- retrieval hit rate: 96.36% (n=357)
- correct abstention: 25.87% (n=143)
- latency median/p95/max ms: 21.79 / 31.26 / 75.08

## Governance

- decisions logged: 500 against 500 tickets processed (reconciles: True)
- guardrail activations: none
- private data in outbound responses: 0

### Fairness: automation rate by customer group

- **customer_region** spread 8.22pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - asia_pacific: 70.59% (n=119)
  - europe: 78.81% (n=151)
  - latin_america: 76.67% (n=60)
  - north_america: 72.94% (n=170)
- **customer_tier** spread 5.77pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - business: 71.34% (n=164)
  - enterprise: 77.11% (n=83)
  - standard: 75.89% (n=253)
- **language_fluency** spread 7.15pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - fluent: 76.32% (n=380)
  - non_fluent: 69.17% (n=120)
