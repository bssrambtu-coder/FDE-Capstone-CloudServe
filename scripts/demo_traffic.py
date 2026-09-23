"""Feed tickets into the running local API at a steady pace.

    python -m src.api --backend hybrid                  # terminal 1
    python scripts/demo_traffic.py --rate 1             # terminal 2

Monitoring needs traffic to show anything. A batch run finishes in under a
second, well inside one Prometheus scrape, so the dashboard would stay flat;
this instead replays the validation set through `POST /tickets` one ticket at a
time, looping, so the panels fill in over a minute or two the way they would
under real load.

Each ticket is sent exactly as a customer's would be: evaluation labels and
history are stripped before sending. Standard library only (urllib), so it runs
in either lane. Stops cleanly on Ctrl+C and prints what it sent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from src import console as c  # noqa: E402

c.setup()

SETS = {"validation": "Capstone_Pack/05_Datasets/validation_tickets.json",
        "development": "Capstone_Pack/05_Datasets/development_tickets.json"}
FIELDS = ("ticket_id", "channel", "subject", "body", "received_at", "customer_id",
          "customer_tier", "customer_region", "language_fluency")


def post(url: str, payload: dict, timeout: float = 60) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Replay tickets into the local API.")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--rate", type=float, default=1.0, help="tickets per second")
    ap.add_argument("--count", type=int, default=0, help="stop after this many (0 = until Ctrl+C)")
    ap.add_argument("--dataset", choices=tuple(SETS), default="validation")
    args = ap.parse_args(argv)

    tickets = json.loads((ROOT / SETS[args.dataset]).read_text(encoding="utf-8"))
    try:
        with urllib.request.urlopen(f"{args.api}/health", timeout=5) as r:
            health = json.loads(r.read())
    except (urllib.error.URLError, OSError):
        print(c.style(f"  ✖ No API at {args.api}. Start it first:  python -m src.api --backend hybrid",
                      "bold", "bred"))
        return 2

    semantic = health.get("semantic_retrieval_available")
    c.banner("Replaying tickets into the local API",
             f"{args.dataset} set · {args.rate:g} per second · "
             f"semantic retrieval {'ON' if semantic else 'OFF'} · Ctrl+C to stop")
    if not semantic:
        print(c.style("  ⚠ The API is running lexical-only; the dashboard's R-07 alarm will fire.", "byellow"))

    tally: Counter = Counter()
    sent, gap = 0, 1.0 / args.rate if args.rate > 0 else 0
    started = time.perf_counter()
    try:
        while not args.count or sent < args.count:
            t = tickets[sent % len(tickets)]
            payload = {k: str(t.get(k) or "") for k in FIELDS}
            tick = time.perf_counter()
            status, body = post(f"{args.api}/tickets", payload)
            sent += 1
            if status == 200:
                action = body.get("action", "?")
                tally[action] += 1
                print(f"  {c.style(f'{sent:>5}', 'grey')}  {payload['ticket_id']:<10} "
                      f"{payload['channel']:<13} {c.action_badge(action)}  {c.style(body.get('rule', ''), 'grey')}")
            else:
                tally["http_" + str(status)] += 1
                print(c.style(f"  {sent:>5}  {payload['ticket_id']:<10} HTTP {status}  "
                              f"{body.get('detail', '')}", "bred"))
            time.sleep(max(0.0, gap - (time.perf_counter() - tick)))
    except KeyboardInterrupt:
        pass
    except (urllib.error.URLError, OSError) as exc:
        print(c.style(f"\n  ✖ Lost the API: {exc}", "bold", "bred"))
    elapsed = time.perf_counter() - started
    print()
    print("  " + "  ·  ".join(f"{k} {v}" for k, v in sorted(tally.items())) +
          c.style(f"   ({sent} sent in {elapsed:.0f}s)", "grey"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
