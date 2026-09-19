# Evaluation metrics

## Volume

- tickets processed: 80
- answered automatically: 56
- escalated: 24
- blocked by guardrails: 0
- pipeline errors: 0
- degraded responses: 0

## Business outcomes

- automation rate pct: 70.0
- first contact resolution pct: None
- escalation rate pct: 30.0
- mean response time ms: 20.25
- median response time ms: 21.01
- customer satisfaction: None
- repeat contacts: None

Resolution requires customer follow-up; automatic answers are not confirmed resolutions.

## Technical performance

- intent accuracy: 1.0
- intent macro precision: 1.0
- urgency accuracy: 0.475
- retrieval hit rate: 98.11% (n=53)
- correct abstention: 33.33% (n=27)
- latency median/p95/max ms: 21.01 / 30.73 / 36.59

## Governance

- decisions logged: 80 against 80 tickets processed (reconciles: True)
- guardrail activations: none
- private data in outbound responses: 0

### Fairness: automation rate by customer group

- **customer_region** spread 47.62pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - asia_pacific: 76.19% (n=21)
  - europe: 76.0% (n=25)
  - latin_america: 28.57% (n=7)
  - north_america: 70.37% (n=27)
- **customer_tier** spread 27.14pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - business: 86.67% (n=30)
  - enterprise: 62.5% (n=8)
  - standard: 59.52% (n=42)
- **language_fluency** spread 15.88pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - fluent: 73.77% (n=61)
  - non_fluent: 57.89% (n=19)
