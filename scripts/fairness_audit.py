#!/usr/bin/env python3
"""Fairness audit: is the automation-rate gap between customer groups caused
by the system treating groups differently, or by their intent mix differing?

The governance framework sets a five percentage point condition on quality
across customer groups. A raw spread that breaches it is not by itself
evidence of unfair treatment: if one group sends proportionally more
compliance requests, and compliance requests are always escalated by policy,
that group's automation rate falls for a reason that is correct.

The audit therefore reports three things per group:

  observed     the raw automation rate
  expected     what the rate would be if the system treated every group
               identically and only the intent mix differed
  residual     observed - expected, which is the part attributable to
               differential treatment rather than composition

Residual is the number the condition should be judged on. Wilson intervals are
reported because some groups are small and a wide interval is not a finding.

    python3 scripts/fairness_audit.py [--input <tickets.json>] [--output <dir>]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import pathlib
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.ingest import load_tickets      # noqa: E402
from src.pipeline import Pipeline        # noqa: E402

FIELDS = ("customer_tier", "customer_region", "language_fluency")
TOLERANCE_PP = 5.0
DEV = "Capstone_Pack/05_Datasets/development_tickets.json"


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Behaves sensibly at n=7, which the normal
    approximation does not."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * max(0.0, centre - half), 100 * min(1.0, centre + half))


def two_proportion_p(s1: int, n1: int, s2: int, n2: int) -> float | None:
    """Two-sided z-test for a difference in proportions."""
    if not n1 or not n2:
        return None
    p = (s1 + s2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = abs(s1 / n1 - s2 / n2) / se
    return round(2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))), 4)


def audit(rows: list[dict], field: str) -> dict:
    """rows: {group, intent, automated: bool, label_automated: bool|None}"""
    # Overall automation rate per intent, pooled across every group. This is
    # the "identical treatment" reference.
    per_intent = defaultdict(lambda: [0, 0])
    for r in rows:
        per_intent[r["intent"]][0] += 1
        per_intent[r["intent"]][1] += int(r["automated"])
    intent_rate = {i: a / t for i, (t, a) in per_intent.items() if t}

    groups = defaultdict(list)
    for r in rows:
        groups[r[field]].append(r)

    out = {}
    for group, members in sorted(groups.items()):
        n = len(members)
        automated = sum(int(r["automated"]) for r in members)
        mix = Counter(r["intent"] for r in members)
        expected = sum(count * intent_rate.get(i, 0.0) for i, count in mix.items()) / n
        labelled = [r for r in members if r["label_automated"] is not None]
        lo, hi = wilson(automated, n)
        out[group] = {
            "n": n,
            "observed_pct": round(100 * automated / n, 2),
            "expected_pct": round(100 * expected, 2),
            "residual_pp": round(100 * (automated / n - expected), 2),
            "wilson_95_pct": [round(lo, 2), round(hi, 2)],
            "label_implied_pct": (
                round(100 * sum(int(r["label_automated"]) for r in labelled) / len(labelled), 2)
                if labelled else None
            ),
            "top_intents": [i for i, _ in mix.most_common(3)],
        }

    obs = [v["observed_pct"] for v in out.values()]
    res = [v["residual_pp"] for v in out.values()]
    lab = [v["label_implied_pct"] for v in out.values() if v["label_implied_pct"] is not None]

    # Largest pairwise gap, with a significance test on the raw rates.
    worst = None
    keys = list(out)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            gap = abs(out[a]["observed_pct"] - out[b]["observed_pct"])
            if worst is None or gap > worst["gap_pp"]:
                worst = {
                    "groups": [a, b],
                    "gap_pp": round(gap, 2),
                    "p_value": two_proportion_p(
                        round(out[a]["observed_pct"] * out[a]["n"] / 100), out[a]["n"],
                        round(out[b]["observed_pct"] * out[b]["n"] / 100), out[b]["n"],
                    ),
                }

    return {
        "groups": out,
        "observed_spread_pp": round(max(obs) - min(obs), 2) if len(obs) > 1 else 0.0,
        "residual_spread_pp": round(max(res) - min(res), 2) if len(res) > 1 else 0.0,
        "label_implied_spread_pp": round(max(lab) - min(lab), 2) if len(lab) > 1 else None,
        "largest_gap": worst,
        "tolerance_pp": TOLERANCE_PP,
        "observed_within_tolerance": (max(obs) - min(obs)) <= TOLERANCE_PP if len(obs) > 1 else True,
        "residual_within_tolerance": (max(res) - min(res)) <= TOLERANCE_PP if len(res) > 1 else True,
    }


# The governance condition is on "quality across customer groups". Automation
# rate is a throughput measure, not a quality one: a group could be automated
# more often and served worse. These are the quality measures that matter, and
# each is judged against the same five point condition.
QUALITY = {
    "intent_accuracy": "intent_correct",
    "routing_agreement": "route_agrees",
    "retrieval_hit_rate": "retrieval_hit",
    "citation_present": "cited",
}


def quality(rows: list[dict], field: str) -> dict:
    out = {}
    for name, key in QUALITY.items():
        groups = defaultdict(lambda: [0, 0])
        for r in rows:
            if r.get(key) is None:
                continue
            groups[r[field]][0] += 1
            groups[r[field]][1] += int(r[key])
        rates = {g: 100 * a / t for g, (t, a) in groups.items() if t}
        if len(rates) < 2:
            out[name] = {"measurable": False}
            continue
        spread = max(rates.values()) - min(rates.values())
        out[name] = {
            "measurable": True,
            "by_group_pct": {g: round(v, 2) for g, v in sorted(rates.items())},
            "group_sizes": {g: t for g, (t, _) in sorted(groups.items())},
            "spread_pp": round(spread, 2),
            "within_tolerance": spread <= TOLERANCE_PP,
        }
    return out


def within_intent(rows: list[dict], field: str, *, min_n: int = 12) -> list[dict]:
    """For each intent with enough data, compare groups directly. If the
    system treats groups identically, these gaps are all near zero."""
    by_intent = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in rows:
        cell = by_intent[r["intent"]][r[field]]
        cell[0] += 1
        cell[1] += int(r["automated"])
    findings = []
    for intent, groups in sorted(by_intent.items()):
        usable = {g: c for g, c in groups.items() if c[0] >= min_n}
        if len(usable) < 2:
            continue
        rates = {g: 100 * a / t for g, (t, a) in usable.items()}
        hi = max(rates, key=rates.get)
        lo = min(rates, key=rates.get)
        gap = rates[hi] - rates[lo]
        if gap > 0.01:
            findings.append({
                "intent": intent,
                "gap_pp": round(gap, 2),
                "highest": [hi, round(rates[hi], 2), usable[hi][0]],
                "lowest": [lo, round(rates[lo], 2), usable[lo][0]],
                "p_value": two_proportion_p(usable[hi][1], usable[hi][0],
                                            usable[lo][1], usable[lo][0]),
            })
    return sorted(findings, key=lambda f: -f["gap_pp"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=DEV)
    ap.add_argument("--output", default="evaluation/fairness")
    ap.add_argument("--min-cell", type=int, default=12)
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)

    tickets = list(load_tickets(args.input))
    pipe = Pipeline()
    rows = []
    for t in tickets:
        o = pipe.process(t)
        labels = t.labels
        expected_route = labels.get("expected_route")
        expected_docs = set(labels.get("expected_doc_ids") or [])
        rows.append({
            "ticket_id": t.ticket_id,
            "customer_tier": t.customer_tier,
            "customer_region": t.customer_region,
            "language_fluency": t.language_fluency,
            "intent": o.intent or "unknown",
            "automated": o.action == "answered",
            "label_automated": (expected_route == "auto_respond") if expected_route else None,
            # quality measures, each None when the input carries no label
            "intent_correct": (o.intent == labels["intent"]) if labels.get("intent") else None,
            "route_agrees": ((o.action == "answered") == (expected_route == "auto_respond"))
                            if expected_route else None,
            "retrieval_hit": (bool(set(o.sources) & expected_docs) if expected_docs else None),
            "cited": bool(o.citations),
        })

    report = {
        "input": args.input,
        "tickets": len(rows),
        "automation_rate_pct": round(100 * sum(r["automated"] for r in rows) / len(rows), 2),
        "quality_by_group": {f: quality(rows, f) for f in FIELDS},
        "dimensions": {},
    }
    for field in FIELDS:
        report["dimensions"][field] = audit(rows, field)
        report["dimensions"][field]["within_intent"] = within_intent(
            rows, field, min_n=args.min_cell
        )

    out = pathlib.Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "fairness.json").write_text(json.dumps(report, indent=1), encoding="utf-8")

    print(f"input: {args.input}   n={len(rows)}   overall automation "
          f"{report['automation_rate_pct']}%\n")
    print("QUALITY MEASURES (the condition is on quality, not throughput)")
    for field, measures in report["quality_by_group"].items():
        print(f"\n=== {field}")
        for name, m in measures.items():
            if not m.get("measurable"):
                print(f"  {name:20} not measurable (no labels)")
                continue
            flag = "ok  " if m["within_tolerance"] else "FAIL"
            cells = "  ".join(f"{g}={v}%" for g, v in m["by_group_pct"].items())
            print(f"  {name:20} spread {m['spread_pp']:>6.2f}pp [{flag}]  {cells}")
    print("\n" + "=" * 78)
    print("AUTOMATION RATE DECOMPOSITION\n")

    for field, d in report["dimensions"].items():
        print(f"=== {field}")
        print(f"{'group':16} {'n':>5} {'observed':>9} {'expected':>9} {'residual':>9}  {'95% CI':>16}  label")
        for g, v in d["groups"].items():
            ci = f"[{v['wilson_95_pct'][0]:.0f},{v['wilson_95_pct'][1]:.0f}]"
            print(f"  {g:14} {v['n']:>5} {v['observed_pct']:>8.1f}% {v['expected_pct']:>8.1f}% "
                  f"{v['residual_pp']:>+8.1f}  {ci:>16}  {v['label_implied_pct']}")
        print(f"  spread: observed {d['observed_spread_pp']}pp | "
              f"residual {d['residual_spread_pp']}pp | "
              f"label-implied {d['label_implied_spread_pp']}pp")
        w = d["largest_gap"]
        print(f"  largest gap: {w['groups'][0]} vs {w['groups'][1]} = {w['gap_pp']}pp "
              f"(p={w['p_value']})")
        verdict = "PASS" if d["residual_within_tolerance"] else "FAIL"
        print(f"  residual verdict against {TOLERANCE_PP}pp condition: {verdict}")
        if d["within_intent"]:
            print(f"  within-intent gaps (cells >= {args.min_cell}):")
            for f in d["within_intent"][:4]:
                print(f"    {f['intent']:24} {f['gap_pp']:>6.1f}pp  "
                      f"{f['highest'][0]}={f['highest'][1]}% (n={f['highest'][2]}) vs "
                      f"{f['lowest'][0]}={f['lowest'][1]}% (n={f['lowest'][2]})  p={f['p_value']}")
        else:
            print(f"  within-intent gaps: none (no cell reached n={args.min_cell})")
        print()
    print(f"wrote {out / 'fairness.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
