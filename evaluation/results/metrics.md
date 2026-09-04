# Evaluation metrics

## Volume

- tickets processed: 80
- answered automatically: 52
- escalated: 28
- blocked by guardrails: 0
- pipeline errors: 0
- degraded responses: 0

## Business outcomes

- first contact resolution pct: 65.0
- escalation rate pct: 35.0
- mean response time ms: 0.37
- median response time ms: 0.4
- customer satisfaction: None
- repeat contacts: None

## Technical performance

- intent accuracy: 1.0
- intent macro precision: 1.0
- urgency accuracy: 0.475
- retrieval hit rate: 79.25% (n=53)
- correct abstention: 55.56% (n=27)
- latency median/p95/max ms: 0.4 / 0.73 / 0.98

## Governance

- decisions logged: 80 against 80 tickets processed (reconciles: True)
- guardrail activations: {'unsupported_claims': 1}
- private data in outbound responses: 0

### Fairness: automation rate by customer group

- **customer_region** spread 9.52pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - asia_pacific: 66.67% (n=21)
  - europe: 64.0% (n=25)
  - latin_america: 57.14% (n=7)
  - north_america: 66.67% (n=27)
- **customer_tier** spread 27.62pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - business: 80.0% (n=30)
  - enterprise: 75.0% (n=8)
  - standard: 52.38% (n=42)
- **language_fluency** spread 2.42pp (within tolerance, tolerance 5.0pp)
  - fluent: 65.57% (n=61)
  - non_fluent: 63.16% (n=19)
