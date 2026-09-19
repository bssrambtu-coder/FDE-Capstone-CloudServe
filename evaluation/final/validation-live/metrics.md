# Evaluation metrics

## Volume

- tickets processed: 80
- answered automatically: 54
- escalated: 24
- blocked by guardrails: 2
- pipeline errors: 0
- degraded responses: 4

## Business outcomes

- automation rate pct: 67.5
- first contact resolution pct: None
- escalation rate pct: 32.5
- mean response time ms: 11742.24
- median response time ms: 11219.06
- customer satisfaction: None
- repeat contacts: None

Resolution requires customer follow-up; automatic answers are not confirmed resolutions.

## Technical performance

- intent accuracy: 1.0
- intent macro precision: 1.0
- urgency accuracy: 0.475
- retrieval hit rate: 98.11% (n=53)
- correct abstention: 33.33% (n=27)
- latency median/p95/max ms: 11219.06 / 23144.31 / 49046.5

## Governance

- decisions logged: 80 against 80 tickets processed (reconciles: True)
- guardrail activations: {'unsupported_claims': 2}
- private data in outbound responses: 0

### Fairness: automation rate by customer group

- **customer_region** spread 43.43pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - asia_pacific: 71.43% (n=21)
  - europe: 72.0% (n=25)
  - latin_america: 28.57% (n=7)
  - north_america: 70.37% (n=27)
- **customer_tier** spread 26.19pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - business: 83.33% (n=30)
  - enterprise: 62.5% (n=8)
  - standard: 57.14% (n=42)
- **language_fluency** spread 12.6pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - fluent: 70.49% (n=61)
  - non_fluent: 57.89% (n=19)
