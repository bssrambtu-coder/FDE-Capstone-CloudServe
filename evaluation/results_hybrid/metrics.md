# Evaluation metrics

## Volume

- tickets processed: 80
- answered automatically: 65
- escalated: 15
- blocked by guardrails: 0
- pipeline errors: 0
- degraded responses: 0

## Business outcomes

- first contact resolution pct: 81.25
- escalation rate pct: 18.75
- mean response time ms: 9.9
- median response time ms: 8.79
- customer satisfaction: None
- repeat contacts: None

## Technical performance

- intent accuracy: 1.0
- intent macro precision: 1.0
- urgency accuracy: 0.475
- retrieval hit rate: 98.11% (n=53)
- correct abstention: 33.33% (n=27)
- latency median/p95/max ms: 8.79 / 11.37 / 148.06

## Governance

- decisions logged: 80 against 80 tickets processed (reconciles: True)
- guardrail activations: {'unsupported_claims': 24}
- private data in outbound responses: 0

### Fairness: automation rate by customer group

- **customer_region** spread 33.33pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - asia_pacific: 90.48% (n=21)
  - europe: 84.0% (n=25)
  - latin_america: 57.14% (n=7)
  - north_america: 77.78% (n=27)
- **customer_tier** spread 21.9pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - business: 93.33% (n=30)
  - enterprise: 87.5% (n=8)
  - standard: 71.43% (n=42)
- **language_fluency** spread 3.88pp (within tolerance, tolerance 5.0pp)
  - fluent: 80.33% (n=61)
  - non_fluent: 84.21% (n=19)
