# Evaluation metrics

## Volume

- tickets processed: 80
- answered automatically: 54
- escalated: 26
- blocked by guardrails: 0
- pipeline errors: 0
- degraded responses: 0

## Business outcomes

- first contact resolution pct: 67.5
- escalation rate pct: 32.5
- mean response time ms: 0.24
- median response time ms: 0.27
- customer satisfaction: None
- repeat contacts: None

## Technical performance

- intent accuracy: 1.0
- intent macro precision: 1.0
- urgency accuracy: 0.475
- retrieval hit rate: 77.36% (n=53)
- correct abstention: 44.44% (n=27)
- latency median/p95/max ms: 0.27 / 0.46 / 0.55

## Governance

- decisions logged: 80 against 80 tickets processed (reconciles: True)
- guardrail activations: none
- private data in outbound responses: 0

### Fairness: automation rate by customer group

- **customer_region** spread 16.93pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - asia_pacific: 61.9% (n=21)
  - europe: 68.0% (n=25)
  - latin_america: 57.14% (n=7)
  - north_america: 74.07% (n=27)
- **customer_tier** spread 27.98pp (EXCEEDS TOLERANCE, tolerance 5.0pp)
  - business: 73.33% (n=30)
  - enterprise: 87.5% (n=8)
  - standard: 59.52% (n=42)
- **language_fluency** spread 1.21pp (within tolerance, tolerance 5.0pp)
  - fluent: 67.21% (n=61)
  - non_fluent: 68.42% (n=19)
