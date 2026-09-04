#!/usr/bin/env python3
"""Roll effort_log.csv up into the Effort_Log.docx tables.

Generates Section 2 (stage summary), Section 6 (estimates vs reality) and
Section 8 (total hours), checks the logging cadence the template requires
(an entry at least every second day), and summarises the evaluation runs and
AI tool use the report has to declare.

    python3 effort/rollup.py
"""

import csv
import datetime as dt
import pathlib
import sys
from collections import defaultdict

HERE = pathlib.Path(__file__).parent
CADENCE_DAYS = 2          # template: "fill in every second day"
BUILD_STAGE = "Build and revision"
BUILD_SHARE_WARN = 0.50   # pack guidance: build is about half the project


def read(name):
    path = HERE / name
    if not path.exists():
        print(f"  ! missing {name}", file=sys.stderr)
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]


def hours(row, field="hours"):
    try:
        return float(row.get(field) or 0)
    except ValueError:
        print(f"  ! unparseable {field}={row.get(field)!r} on {row.get('date') or row.get('item')}",
              file=sys.stderr)
        return 0.0


def table(headers, rows):
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
              for i, h in enumerate(headers)]
    out = ["| " + " | ".join(str(h).ljust(w) for h, w in zip(headers, widths)) + " |",
           "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).ljust(w) for c, w in zip(r, widths)) + " |")
    return "\n".join(out)


def main():
    log = read("effort_log.csv")
    planned = {r["stage"]: hours(r, "planned_hours") for r in read("planned_baseline.csv")}
    estimates = {r["item"]: hours(r, "estimated_hours") for r in read("sprint_estimates.csv")}
    runs = read("eval_runs.csv")

    if not log:
        print("effort_log.csv has no entries yet.")
        return 1

    actual = defaultdict(float)
    for r in log:
        actual[(r["stage"] or "").strip()] += hours(r)

    # stage names in the log that the frozen baseline does not know about
    unknown = sorted(set(actual) - set(planned))

    print("## Section 2 — Summary of hours by stage\n")
    rows, tp, ta = [], 0.0, 0.0
    for stage, p in planned.items():
        a = actual.get(stage, 0.0)
        tp, ta = tp + p, ta + a
        rows.append([stage, f"{p:g}", f"{a:g}", f"{a - p:+g}", ""])
    for stage in unknown:
        a = actual[stage]
        ta += a
        rows.append([f"{stage}  (NOT IN BASELINE)", "-", f"{a:g}", "-", ""])
    rows.append(["Total", f"{tp:g}", f"{ta:g}", f"{ta - tp:+g}", ""])
    print(table(["Stage", "Planned", "Actual", "Difference", "Why the difference"], rows))
    print("\n(The 'why' column is yours to write — it is the part that earns marks.)")

    if unknown:
        print(f"\n  ! stage names not in planned_baseline.csv: {unknown}")
        print("  ! fix the typo in effort_log.csv, or these hours will not reconcile.")

    print("\n## Section 6 — Estimates against reality\n")
    by_item = defaultdict(float)
    for r in log:
        item = (r.get("sprint_item") or "").strip()
        if item:
            by_item[item] += hours(r)
    if by_item:
        top = sorted(by_item.items(), key=lambda kv: -kv[1])[:5]
        print(table(["Item", "Estimated", "Actual", "Why the difference"],
                    [[i, f"{estimates.get(i, 0):g}" if i in estimates else "?", f"{a:g}", ""]
                     for i, a in top]))
        missing = [i for i, _ in top if i not in estimates]
        if missing:
            print(f"\n  ! no estimate in sprint_estimates.csv for: {missing}")
    else:
        print("No sprint_item values logged yet — fill these in once Stage 4 exists.")

    print("\n## Section 8 — Declaration\n")
    print(f"Total hours recorded: {ta:g}")

    print("\n## Cadence check (template requires an entry every second day)\n")
    dates = sorted({dt.date.fromisoformat(r["date"]) for r in log if r.get("date")})
    print(f"Distinct days logged: {len(dates)}   first {dates[0]}   last {dates[-1]}")
    gaps = [(a, b) for a, b in zip(dates, dates[1:]) if (b - a).days > CADENCE_DAYS]
    if gaps:
        for a, b in gaps:
            print(f"  ! gap of {(b - a).days} days between {a} and {b}")
    else:
        print("  ok — no gap longer than two days.")
    today = dt.date.today()
    if (today - dates[-1]).days > CADENCE_DAYS:
        print(f"  ! {(today - dates[-1]).days} days since the last entry ({dates[-1]}).")

    print("\n## Distribution against the pack's guidance\n")
    if ta:
        share = actual.get(BUILD_STAGE, 0.0) / ta
        print(f"{BUILD_STAGE}: {share:.0%} of {ta:g}h logged")
        if share > BUILD_SHARE_WARN:
            print("  ! over half your hours are in the build — the pack warns this means "
                  "something upstream was left unfinished.")

    print("\n## Evaluation runs (the report must state the date and the count)\n")
    if runs:
        per = defaultdict(int)
        for r in runs:
            per[r.get("dataset", "?")] += 1
        for ds, n in sorted(per.items()):
            when = [r["date"] for r in runs if r.get("dataset") == ds]
            print(f"  {ds}: {n} run(s) — {', '.join(when)}")
    else:
        print("  none logged yet.")

    print("\n## AI tool use (feeds the report declaration)\n")
    tools = defaultdict(float)
    for r in log:
        for t in (r.get("ai_tools") or "").split(";"):
            if t.strip():
                tools[t.strip()] += hours(r)
    if tools:
        for t, h in sorted(tools.items(), key=lambda kv: -kv[1]):
            print(f"  {t}: touched {h:g}h of logged work")
        print("\n  Override/correction notes:")
        for r in log:
            if (r.get("ai_notes") or "").strip():
                print(f"    {r['date']}: {r['ai_notes']}")
    else:
        print("  none logged yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
