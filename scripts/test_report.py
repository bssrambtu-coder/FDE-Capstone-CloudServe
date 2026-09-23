"""The test suite, grouped by what each group proves.

    python scripts/test_report.py            # run, print grouped results, write the HTML report
    python scripts/test_report.py --open     # and open the report in a browser

Runs exactly the tests `python -m unittest discover -s tests` runs, through
unittest itself, so the verdict is the same one. What changes is how it reads:
tests are grouped under the acceptance criterion or release concern they
cover, each test's name is written out as the sentence it already is
(`test_private_data_blocks` becomes "Private data blocks"), and every skip
states its reason. That last part matters for explaining the two lanes: without
the optional packages, nine tests skip themselves on purpose, and the report
says which and why rather than leaving an "s" in a row of dots.

Writes artifacts/test_report.html, a self-contained page with no external
requests. Exits non-zero if anything failed, like unittest does.
"""

from __future__ import annotations

import argparse
import html
import logging
import os
import platform
import sys
import time
import traceback
import unittest
import webbrowser
from datetime import datetime
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from src import console as c  # noqa: E402

c.setup()
logging.basicConfig(level=logging.CRITICAL)

# Class name -> (short code, what the group proves). Order here is report order.
GROUPS = {
    "A2Ingest": ("A2", "Ingest: four channels become one representation"),
    "A3Classify": ("A3", "Classify: intent, urgency and a confidence that is never certainty"),
    "A4Retrieve": ("A4", "Retrieve: something relevant, or nothing"),
    "A4HybridRetrieval": ("A4", "Hybrid retrieval, the audited configuration"),
    "A5RoutingDeterminism": ("A5", "Routing: deterministic, with a readable reason"),
    "A6Citations": ("A6", "Citations resolve to what was retrieved"),
    "A7Guardrails": ("A7", "Guardrails block rather than warn"),
    "A9A10HarnessRun": ("A9–A10", "The unattended run over a file never seen before"),
    "A11Resilience": ("A11", "Failure handling: degrade, never crash"),
    "B12Monitoring": ("B12", "Monitoring counts every ticket, including failures"),
    "SafetyRegressions": ("Safety", "Release safety regressions from the 18 September review"),
    "ReleaseTests": ("Release", "Audit durability and policy boundaries"),
    "ControlTests": ("Pause", "The operator pause control"),
    "ApiTests": ("API", "The local API's contract"),
    "OpenRouterTests": ("Provider", "The OpenRouter contract, tested offline"),
    "GuardrailDemoIsReal": ("Demo", "The demo's guardrail block comes from the real validator"),
    "DemoPage": ("Demo", "The demo page: offline, label-free, no pause control"),
    "TestReportCountsWhatUnittestCounts": ("Demo", "This report counts what unittest counts"),
    "DashboardCopyIsInSync": ("Demo", "The provisioned dashboard matches its source"),
    "TrafficSendsOnlyCustomerFields": ("Demo", "Demo traffic sends only what a customer would"),
}

ACRONYMS = {"api": "API", "id": "ID", "ids": "IDs", "jsonl": "JSONL", "json": "JSON", "sqlite": "SQLite",
            "mfa": "MFA", "url": "URL", "http": "HTTP", "openrouter": "OpenRouter", "pii": "PII",
            "env": "env", "a11": "A11", "llm": "LLM"}

EXTRAS = (("chromadb", "chromadb"), ("sentence-transformers", "sentence_transformers"),
          ("fastapi", "fastapi"), ("prometheus-client", "prometheus_client"))


def sentence(method: str) -> str:
    words = method.removeprefix("test_").split("_")
    words = [ACRONYMS.get(w, w) for w in words]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


class Recorder(unittest.TestResult):
    """Keeps every outcome, with timing and the reason for any skip."""

    def __init__(self):
        super().__init__()
        self.records: list[dict] = []
        self._started = 0.0
        self.buffer = True  # test prints and logs stay out of the report

    def startTest(self, test):
        self._started = time.perf_counter()
        super().startTest(test)

    def _add(self, test, status, detail=""):
        method = getattr(test, "_testMethodName", str(test))
        doc = (getattr(test, method, None).__doc__ or "") if hasattr(test, method) else ""
        # A test class that inherits another's tests runs them again under its
        # own setUp. Say so, so a repeated name reads as what it is.
        owner = next((k.__name__ for k in type(test).__mro__ if method in vars(k)), type(test).__name__)
        self.records.append({
            "inherited_from": owner if owner != type(test).__name__ else "",
            "module": type(test).__module__, "cls": type(test).__name__, "method": method,
            "name": sentence(method), "doc": " ".join(doc.split()), "status": status,
            "detail": detail, "seconds": time.perf_counter() - self._started,
        })

    def addSuccess(self, test):
        super().addSuccess(test)
        self._add(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._add(test, "fail", "".join(traceback.format_exception(*err)))

    def addError(self, test, err):
        super().addError(test, err)
        self._add(test, "error", "".join(traceback.format_exception(*err)))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._add(test, "skip", reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._add(test, "pass", "expected failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._add(test, "fail", "unexpected success")


def distinct(records) -> int:
    """Test methods counted once, however many classes run them."""
    return len({(r["module"], r["inherited_from"] or r["cls"], r["method"]) for r in records})


def grouped(records):
    order = list(GROUPS)
    buckets: dict[str, list] = {}
    for r in records:
        buckets.setdefault(r["cls"], []).append(r)
    keys = sorted(buckets, key=lambda k: (order.index(k) if k in order else len(order), k))
    for k in keys:
        code, title = GROUPS.get(k, (k, sentence(k)))
        yield code, title, buckets[k]


def environment() -> dict:
    installed = {}
    for dist, _ in EXTRAS:
        try:
            installed[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            installed[dist] = None
    lane = "full stack (optional packages installed)" if all(installed.values()) else (
        "standard-library spine (optional packages absent)" if not any(installed.values())
        else "partial (some optional packages installed)")
    return {"python": platform.python_version(), "os": f"{platform.system()} {platform.release()}",
            "installed": installed, "lane": lane, "when": datetime.now().strftime("%d %B %Y, %H:%M")}


def print_console(records, env, elapsed):
    mark = {"pass": c.style("✓", "bgreen"), "skip": c.style("○", "byellow"),
            "fail": c.style("✖", "bred"), "error": c.style("✖", "bred")}
    c.banner("Test suite, grouped by what it proves",
             f"Python {env['python']} on {env['os']} · {env['lane']}")
    for code, title, rows in grouped(records):
        passed = sum(r["status"] == "pass" for r in rows)
        skipped = sum(r["status"] == "skip" for r in rows)
        tally = f"{passed}/{len(rows)}" + (f"  ({skipped} skipped)" if skipped else "")
        colour = "bred" if any(r["status"] in ("fail", "error") for r in rows) else (
            "byellow" if skipped == len(rows) else "bgreen")
        print()
        print(f"  {c.style(f'{code:<9}', 'bold', 'bcyan')}{c.style(title, 'bold')}  {c.style(tally, colour)}")
        for r in rows:
            line = f"      {mark[r['status']]} {r['name']}"
            if r["inherited_from"]:
                line += c.style(f"  (from {r['inherited_from']}, run again under this setup)", "grey")
            if r["status"] == "skip":
                line += c.style(f"  — skipped: {r['detail']}", "byellow")
            print(line)
            if r["status"] in ("fail", "error"):
                last = [l for l in r["detail"].strip().splitlines() if l.strip()][-1:]
                print(c.style(f"          {last[0] if last else r['status']}", "bred"))
    counts = {s: sum(r["status"] == s for r in records) for s in ("pass", "skip", "fail", "error")}
    failed = counts["fail"] + counts["error"]
    print()
    print(c.rule())
    summary = (f"  {c.style(str(counts['pass']) + ' passed', 'bold', 'bgreen')}  ·  "
               f"{c.style(str(counts['skip']) + ' skipped', 'bold', 'byellow' if counts['skip'] else 'grey')}  ·  "
               f"{c.style(str(failed) + ' failed', 'bold', 'bred' if failed else 'grey')}"
               f"   in {elapsed:.1f}s   ({len(records)} runs of {distinct(records)} distinct tests)")
    print(summary)
    if counts["skip"] and not failed:
        print(c.style("  Skips are the optional-package tests standing down in this lane, as designed. "
                      "Install requirements.txt to run all of them.", "grey"))


def write_html(records, env, elapsed, path: Path) -> None:
    e = html.escape
    counts = {s: sum(r["status"] == s for r in records) for s in ("pass", "skip", "fail", "error")}
    failed = counts["fail"] + counts["error"]
    verdict = ("All tests passed" if not failed and not counts["skip"] else
               "All tests that ran passed" if not failed else f"{failed} test(s) failed")
    icon = {"pass": "✓", "skip": "○", "fail": "✖", "error": "✖"}
    sections = []
    for code, title, rows in grouped(records):
        passed = sum(r["status"] == "pass" for r in rows)
        skipped = sum(r["status"] == "skip" for r in rows)
        bad = any(r["status"] in ("fail", "error") for r in rows)
        state = "bad" if bad else ("skip" if skipped == len(rows) else "ok")
        items = []
        for r in rows:
            extra = ""
            if r["status"] == "skip":
                extra = f'<div class="why">Skipped: {e(r["detail"])}</div>'
            elif r["status"] in ("fail", "error"):
                extra = f'<details open><summary>Traceback</summary><pre>{e(r["detail"])}</pre></details>'
            doc = f'<div class="doc">{e(r["doc"])}</div>' if r["doc"] else ""
            if r["inherited_from"]:
                doc += f'<div class="doc">Inherited from {e(r["inherited_from"])}, run again under this class\'s setup.</div>'
            items.append(f'<li class="{r["status"]}"><span class="i">{icon[r["status"]]}</span>'
                         f'<div><div class="n">{e(r["name"])}</div>{doc}{extra}'
                         f'<div class="m">{e(r["module"])}.{e(r["cls"])}.{e(r["method"])} · {r["seconds"] * 1000:.0f} ms</div>'
                         f'</div></li>')
        tally = f"{passed}/{len(rows)}" + (f" · {skipped} skipped" if skipped else "")
        sections.append(f'<section class="g {state}"><header><span class="code">{e(code)}</span>'
                        f'<h2>{e(title)}</h2><span class="tally">{tally}</span></header>'
                        f'<ul>{"".join(items)}</ul></section>')
    pkgs = "".join(f'<span class="pkg {"on" if v else "off"}">{e(k)} {e(v) if v else "absent"}</span>'
                   for k, v in env["installed"].items())
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Test report · CloudServe triage</title>
<style>
:root{{--ink:#14213d;--muted:#5b6475;--line:#dde2ea;--ground:#f3f5f9;--navy:#1b2a4a;--accent:#e8743b;
--ok:#137a3f;--ok-bg:#e6f4ec;--warn:#9a5b00;--warn-bg:#fff3dc;--bad:#b3261e;--bad-bg:#fde8e7;
--mono:ui-monospace,"Cascadia Mono",Consolas,Menlo,monospace}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--ground);color:var(--ink);font:16px/1.5 "Segoe UI",system-ui,-apple-system,Roboto,sans-serif}}
.top{{background:var(--navy);color:#fff;padding:22px 32px}}.top h1{{margin:0;font-size:24px}}.top h1 span{{color:var(--accent)}}
.top p{{margin:6px 0 0;opacity:.85;font-size:14.5px}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px 32px 60px}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:10px}}
.tile{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 18px}}
.tile b{{display:block;font-size:34px;line-height:1.1}}.tile span{{color:var(--muted);font-size:13px;text-transform:uppercase;letter-spacing:1px}}
.tile.ok b{{color:var(--ok)}}.tile.skip b{{color:var(--warn)}}.tile.bad b{{color:var(--bad)}}
.verdict{{font-size:20px;font-weight:700;margin:18px 0 6px}}.verdict.ok{{color:var(--ok)}}.verdict.bad{{color:var(--bad)}}
.pkgs{{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 22px}}.pkg{{font:13px var(--mono);padding:3px 10px;border-radius:99px}}
.pkg.on{{background:var(--ok-bg);color:var(--ok)}}.pkg.off{{background:var(--warn-bg);color:var(--warn)}}
.note{{color:var(--muted);font-size:14px;margin:0 0 22px}}
.g{{background:#fff;border:1px solid var(--line);border-radius:12px;margin-bottom:16px;overflow:hidden}}
.g header{{display:flex;align-items:center;gap:14px;padding:12px 18px;border-bottom:1px solid var(--line);border-left:5px solid var(--ok)}}
.g.skip header{{border-left-color:var(--warn)}}.g.bad header{{border-left-color:var(--bad)}}
.code{{font:700 13px var(--mono);background:var(--navy);color:#fff;border-radius:6px;padding:3px 9px;white-space:nowrap}}
.g h2{{font-size:17px;margin:0;flex:1}}.tally{{font:600 14px var(--mono);color:var(--muted)}}
ul{{list-style:none;margin:0;padding:6px 0}}li{{display:flex;gap:12px;padding:8px 18px}}li+li{{border-top:1px solid #f0f2f6}}
.i{{font-weight:800;width:18px;flex:none}}li.pass .i{{color:var(--ok)}}li.skip .i{{color:var(--warn)}}li.fail .i,li.error .i{{color:var(--bad)}}
.n{{font-weight:600}}.doc{{color:var(--muted);font-size:14px}}.why{{color:var(--warn);font-size:14px}}
.m{{font:12px var(--mono);color:#98a0ad;margin-top:2px}}
pre{{background:#1e2433;color:#e7ebf3;padding:12px;border-radius:8px;overflow:auto;font-size:12.5px}}
@media(max-width:700px){{.tiles{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body>
<div class="top"><h1>CloudServe <span>support triage</span> · test report</h1>
<p>Python {e(env['python'])} on {e(env['os'])} · {e(env['lane'])} · {e(env['when'])}</p></div>
<div class="wrap">
<div class="tiles"><div class="tile ok"><b>{counts['pass']}</b><span>passed</span></div>
<div class="tile skip"><b>{counts['skip']}</b><span>skipped</span></div>
<div class="tile {'bad' if failed else 'ok'}"><b>{failed}</b><span>failed</span></div>
<div class="tile"><b>{elapsed:.1f}s</b><span>{len(records)} runs · {distinct(records)} distinct</span></div></div>
<div class="verdict {'bad' if failed else 'ok'}">{e(verdict)}</div>
<div class="pkgs">{pkgs}</div>
<p class="note">Same tests, same verdict as <code>python -m unittest discover -s tests</code>. Grouped by the acceptance
criterion each covers. Skipped tests are the optional-package tests standing down when those packages are absent.</p>
{''.join(sections)}
</div></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the tests and write a grouped report.")
    ap.add_argument("--output", default="artifacts/test_report.html")
    ap.add_argument("--open", action="store_true", help="open the HTML report when done")
    args = ap.parse_args(argv)

    suite = unittest.TestLoader().discover("tests", top_level_dir=str(ROOT))
    result = Recorder()
    print(c.style("  Running the test suite…", "grey"), flush=True)
    started = time.perf_counter()
    suite.run(result)
    elapsed = time.perf_counter() - started

    env = environment()
    print_console(result.records, env, elapsed)
    out = ROOT / args.output
    write_html(result.records, env, elapsed, out)
    print(c.style(f"  Report: {out.relative_to(ROOT)}", "grey"))
    if args.open:
        webbrowser.open(out.as_uri())
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
