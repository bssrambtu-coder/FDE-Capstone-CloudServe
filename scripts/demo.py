"""The recorded demonstration, as one command.

    python scripts/demo.py              # every step, pausing between them
    python scripts/demo.py --list       # what each step shows
    python scripts/demo.py --step 3     # one step (repeat --step for several)
    python scripts/demo.py --no-pause   # rehearse end to end without stopping

The Submission Guide asks the live demonstration to show a success, an
escalation, a guardrail blocking and the full unattended run. This walks
through exactly those, plus the operator pause and the failure handling, using
real tickets from the validation set whose outcomes were checked beforehand.

Three rules the demo keeps so that it shows the system rather than a staged
version of it:

* **Every decision comes from the real pipeline.** Classification, retrieval,
  routing, generation and the guardrails all run as they do in production. The
  only substitution is the model in step 3, and it is labelled on screen.
* **Step 3's model is scripted, and says so.** Offline generation copies
  sentences out of the documentation, so it never trips a guardrail, and a live
  model is not guaranteed to misbehave on cue. So step 3 hands the real
  validator a draft that invents a commitment, and then shows the two blocks
  the real DeepSeek model actually produced in the final live run.
* **It cannot leave your system paused.** Step 4 uses its own pause switch and
  always switches it back on, even if the demo is interrupted.

Each step checks its outcome against the one expected. If something differs,
typically because semantic retrieval is not available, it says so on screen
rather than carrying on as if nothing happened.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import replace
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # models/ and storage/ are resolved relative to the repository

from src import console as c  # noqa: E402

c.setup()
# The pipeline logs every provider retry and fallback as a warning. Step 5
# shows those outcomes in a table instead, so the raw lines are held back.
import logging  # noqa: E402

logging.basicConfig(level=logging.ERROR, format="  %(levelname)s %(name)s: %(message)s")

from src.config import CONFIG  # noqa: E402
from src.control import AutomationControl  # noqa: E402
from src.decision_log import DecisionLog  # noqa: E402
from src.ingest import load_tickets  # noqa: E402
from src.monitoring import Metrics  # noqa: E402
from src.pipeline import Pipeline  # noqa: E402
from src.providers import Cache, FaultInjector, ResilientProvider, StubProvider  # noqa: E402

VALIDATION = "Capstone_Pack/05_Datasets/validation_tickets.json"
DEVELOPMENT = "Capstone_Pack/05_Datasets/development_tickets.json"
CORPUS = CONFIG.corpus_path
LIVE_RESULTS = ROOT / "evaluation/final/validation-live/results.json"
WORK = ROOT / "artifacts/demo"

# Each scenario names the outcome it was verified to produce, so the demo can
# notice when the environment differs from the one it was rehearsed in.
SUCCESS = ("VAL-0006", "answered", "confident_and_supported",
           "An authentication problem the documentation answers directly.")
ESCALATIONS = [
    ("VAL-0043", "escalated", "policy_intent",
     "A possible security incident. Always a person, whatever the confidence."),
    ("VAL-0001", "escalated", "sensitive_request",
     "An exposed key. Sensitive-language rules route it to specialist review."),
    ("VAL-0029", "escalated", "no_supporting_documentation",
     "Nothing in the documentation supports an answer, so it declines to guess."),
]
GUARDRAIL = "VAL-0010"


def scripted_bad_draft(cite: str) -> str:
    """What step 3 hands the validator: an invented fee and a promised refund.

    It cites a document the ticket really retrieved, so it gets past the
    citation check and is judged on what it claims. tests/test_demo_tools.py
    holds the real validator to blocking it.
    """
    return ("Your invoice rose because of a one-time platform migration fee, and a refund "
            f"has been issued to your card. [{cite}]")


class Scripted:
    """Stands in for a model. Returns exactly the text it was given."""

    name = "scripted"

    def __init__(self, text: str):
        self.text = text

    def complete(self, prompt: str, *, max_tokens: int = 512) -> str:
        return self.text


class Demo:
    def __init__(self, args):
        self.args = args
        self.tickets = {t.ticket_id: t for t in load_tickets(VALIDATION)}
        self.docs = {d["doc_id"]: d["title"] for d in json.loads(Path(CORPUS).read_text(encoding="utf-8"))}
        WORK.mkdir(parents=True, exist_ok=True)
        # A fresh demo log per session, so the first decision on camera is #1.
        # Only this demo-owned file is removed; storage/decisions.db is never touched.
        (WORK / "decisions.db").unlink(missing_ok=True)
        self.control = AutomationControl(WORK / "demo_paused")
        self.control.enable()
        self.config = replace(CONFIG, kill_switch_path=str(self.control.path),
                              retrieval_backend=args.backend or CONFIG.retrieval_backend)
        self._pipeline = None
        self._log = None
        self.mismatches = 0

    # -- shared machinery ---------------------------------------------------

    @property
    def pipeline(self) -> Pipeline:
        if self._pipeline is None:
            print(c.style("  Loading the classifier and retrieval index…", "grey"), flush=True)
            started = time.perf_counter()
            self._pipeline = Pipeline(config=self.config, control=self.control, metrics=Metrics())
            print(c.style(f"  Ready in {time.perf_counter() - started:.1f}s.", "grey"))
        return self._pipeline

    @property
    def log(self) -> DecisionLog:
        if self._log is None:
            self._log = DecisionLog(str(WORK / "decisions.db"))
            self._log.start_run(input_path="scripts/demo.py", output_path=str(WORK),
                                config={"demo": True, "backend": self.config.retrieval_backend})
        return self._log

    def run_ticket(self, ticket_id: str, *, pipeline: Pipeline | None = None):
        ticket = self.tickets[ticket_id]
        outcome = (pipeline or self.pipeline).process(ticket)
        decision_id = self.log.record(outcome, ticket.text)
        self.log.commit()
        return ticket, outcome, decision_id

    def check(self, outcome, action: str, rule: str | None = None) -> None:
        if outcome.action == action and (rule is None or outcome.rule == rule):
            return
        self.mismatches += 1
        expected = action.upper() + (f" ({rule})" if rule else "")
        got = outcome.action.upper() + f" ({outcome.rule})"
        print(c.style(f"  ⚠ Rehearsed outcome was {expected}; this run gave {got}.", "byellow"))
        if not getattr(self.pipeline.retriever, "semantic_available", False):
            print(c.style("    Semantic retrieval is off, so this is the lexical fallback. "
                          "Install requirements.txt before recording.", "byellow"))

    def show_ticket(self, ticket, note: str = "") -> None:
        p = c.Panel(f"Incoming ticket {ticket.ticket_id}", "bblue")
        p.row("Channel", ticket.channel)
        who = " · ".join(x for x in (ticket.customer_tier, ticket.customer_region,
                                     ticket.language_fluency) if x)
        if who:
            p.row("Customer", who, "grey")
        if ticket.subject:
            p.row("Subject", ticket.subject, "bold")
        body = ticket.body if len(ticket.body) <= 420 else ticket.body[:417] + "…"
        p.row("Message", body)
        if note:
            p.divider().text(note, "italic", "grey")
        p.show()

    def show_outcome(self, outcome, decision_id: int | None = None) -> None:
        colour = {"answered": "bgreen", "escalated": "byellow", "blocked": "bred"}.get(outcome.action, "grey")
        p = c.Panel("Decision", colour)
        p.row("Outcome", f"{c.action_badge(outcome.action)}  rule: {outcome.rule}")
        if outcome.rule == "automation_paused":
            p.row("Read as", "not classified: while paused nothing is scored or sent to a model", "grey")
        elif outcome.intent:
            # Two decimals: at .1% a calibrated 0.9999 prints as 100.0%, which
            # reads as certainty the system never claims (A3).
            conf = f"{outcome.confidence * 100:.2f}%" if outcome.confidence is not None else "n/a"
            p.row("Read as", f"{outcome.intent}  ·  confidence {conf}  ·  urgency {outcome.urgency}")
        p.row("Why", outcome.reason)
        if outcome.sources:
            p.row("Retrieved", "\n".join(f"{d}  {self.docs.get(d, '')}" for d in outcome.sources[:5]), "cyan")
        if outcome.guardrail_findings:
            p.row("Guardrails", "\n".join(outcome.guardrail_findings), "bold", "bred")
        elif outcome.action == "answered":
            p.row("Guardrails", "all checks passed", "bgreen")
        if outcome.degraded:
            p.row("Degraded", "the model failed; this draft is the extractive fallback", "byellow")
        p.divider()
        if outcome.action == "answered":
            p.row("To customer", outcome.response or "", "bgreen", max_lines=self.args.reply_lines)
            if outcome.citations:
                p.row("Cites", ", ".join(outcome.citations), "cyan")
        elif outcome.action == "escalated":
            if outcome.response:
                p.row("For agent", outcome.response, "grey", max_lines=self.args.reply_lines)
                p.row("", "(a draft for the person picking this up; never sent to the customer)", "dim")
            else:
                p.row("For agent", "the ticket, the reason above and the retrieved sources", "grey")
        else:
            p.row("To customer", "NOTHING RELEASED. The draft was withheld.", "bold", "bred")
        if decision_id is not None:
            p.row("Logged", f"decision #{decision_id} in {WORK.relative_to(ROOT) / 'decisions.db'}"
                            f"  ·  {outcome.latency_ms:.0f} ms", "grey")
        p.show()

    # -- the steps ----------------------------------------------------------

    def step_environment(self):
        """What is installed and which configuration is live."""
        p = c.Panel("Environment", "bcyan")
        p.row("Python", f"{platform.python_version()} on {platform.system()} {platform.release()}")
        for label, pkg, role in (("Vector store", "chromadb", "hybrid retrieval"),
                                 ("Embeddings", "sentence-transformers", "all-MiniLM-L6-v2"),
                                 ("API", "fastapi", "local web interface"),
                                 ("Monitoring", "prometheus-client", "metrics exporter")):
            try:
                p.row(label, f"{pkg} {metadata.version(pkg)}  ·  {role}", "bgreen")
            except metadata.PackageNotFoundError:
                p.row(label, f"{pkg} not installed  ·  {role} unavailable", "byellow")
        p.show()
        pipe = self.pipeline
        semantic = bool(getattr(pipe.retriever, "semantic_available", False))
        p = c.Panel("Live configuration", "bcyan")
        p.row("Retrieval", getattr(pipe.retriever, "name", "?"))
        p.row("Semantic half", "available" if semantic else "NOT AVAILABLE", "bgreen" if semantic else "bred")
        p.row("Generation", "extractive (offline; nothing leaves this machine)")
        p.row("Threshold", f"{self.config.confidence_threshold:.2f}  ·  floor {self.config.retrieval_floor}"
                           f"  ·  semantic gate {self.config.semantic_gate}")
        p.row("Corpus", f"{len(self.docs)} documentation articles")
        p.row("Tickets", f"{len(self.tickets)} validation tickets loaded")
        p.show()
        if not semantic:
            print(c.style("\n  ⚠ Hybrid retrieval has fallen back to lexical. That is the configuration "
                          "the fairness audit fails (R-07). Run  pip install -r requirements.txt  "
                          "before recording.", "bold", "bred"))

    def step_success(self):
        """A ticket answered automatically, with citations."""
        tid, action, rule, note = SUCCESS
        ticket, outcome, did = self.run_ticket(tid)
        self.show_ticket(ticket, note)
        self.show_outcome(outcome, did)
        self.check(outcome, action, rule)
        self.show_recorded_live_reply(tid)

    def show_recorded_live_reply(self, ticket_id: str) -> None:
        """The same ticket's reply from the final live-model run, if it has one.

        Offline generation stitches documentation sections together, which is
        safe but reads like a document dump. The live model writes the reply a
        customer would actually get. Shown from the saved run rather than
        called now, so the demo stays offline and the text is exactly what the
        evaluation measured.
        """
        if not LIVE_RESULTS.exists():
            return
        live = {o["ticket_id"]: o for o in json.loads(LIVE_RESULTS.read_text(encoding="utf-8"))}
        o = live.get(ticket_id)
        if not o or o["action"] != "answered" or not o.get("response"):
            return
        c.pause(not self.args.no_pause, "Same ticket, with the live model")
        p = c.Panel("Recorded: this ticket in the final live run (DeepSeek via OpenRouter)", "bgreen")
        p.row("Outcome", f"{c.action_badge(o['action'])}  rule: {o['rule']}")
        p.row("To customer", o["response"], "bgreen")
        p.row("Cites", ", ".join(o.get("citations") or []), "cyan")
        p.divider().text("Saved output from evaluation/final/validation-live, not a new call. "
                         "Same retrieval, same guardrails; only the writer differs.", "grey")
        p.show()

    def step_escalations(self):
        """Three different reasons a ticket goes to a person."""
        for i, (tid, action, rule, note) in enumerate(ESCALATIONS):
            if i:
                c.pause(not self.args.no_pause, "Next escalation")
            ticket, outcome, did = self.run_ticket(tid)
            self.show_ticket(ticket, note)
            self.show_outcome(outcome, did)
            self.check(outcome, action, rule)

    def step_guardrail(self):
        """A draft that invents a commitment is blocked, not sent."""
        ticket, honest, did = self.run_ticket(GUARDRAIL)
        self.show_ticket(ticket, "First, the honest path: the offline generator answers from the documentation.")
        self.show_outcome(honest, did)
        self.check(honest, "answered")
        c.pause(not self.args.no_pause, "Now give it a model that makes something up")

        bad = scripted_bad_draft(honest.sources[0] if honest.sources else "DOC-BILL-001")
        p = c.Panel("SIMULATED MODEL OUTPUT (scripted for this demo)", "magenta")
        p.text("A model that invents a fee and promises a refund. It cites a real document, "
               "so it passes the citation check and reaches the validator:", "grey")
        p.divider().text(bad, "bold")
        p.show()
        base = self.pipeline
        scripted = Pipeline(config=self.config, control=self.control, metrics=Metrics(),
                            classifier=base.classifier, retriever=base.retriever, provider=Scripted(bad))
        _, blocked, did = self.run_ticket(GUARDRAIL, pipeline=scripted)
        self.show_outcome(blocked, did)
        self.check(blocked, "blocked", "guardrail_block")

        if LIVE_RESULTS.exists():
            c.pause(not self.args.no_pause, "And with the real model")
            real = [o for o in json.loads(LIVE_RESULTS.read_text(encoding="utf-8")) if o["action"] == "blocked"]
            p = c.Panel("Evidence: blocks from the real DeepSeek model (final live run, 80 tickets)", "bred")
            for o in real:
                same = "   ← the ticket above" if o["ticket_id"] == GUARDRAIL else ""
                p.row(o["ticket_id"], f"{o['intent']}  →  BLOCKED  ·  {', '.join(o['guardrail_findings'])}{same}")
            p.divider().text("Two of 80 live drafts made a claim their cited source did not support. "
                             "Both were withheld; neither reached a customer.", "grey")
            p.show()

    def step_pause(self):
        """The operator pause: escalate everything, call no model."""
        try:
            self.control.disable()
            p = c.Panel("Operator pause", "byellow")
            p.row("Command", "python -m src.control disable", "bold")
            p.row("Status", "Automatic replies: PAUSED", "byellow")
            p.text("The switch is a file on disk, so it survives restarts and applies to the API "
                   "and to batch runs alike. There is deliberately no remote endpoint for it.", "grey")
            p.show()
            ticket, outcome, did = self.run_ticket(SUCCESS[0])
            self.show_ticket(ticket, "The same ticket that was answered in step 1.")
            self.show_outcome(outcome, did)
            self.check(outcome, "escalated", "automation_paused")
        finally:
            self.control.enable()
        c.pause(not self.args.no_pause, "Resume automatic replies")
        p = c.Panel("Operator pause", "bgreen")
        p.row("Command", "python -m src.control enable", "bold")
        p.row("Status", "Automatic replies: ENABLED", "bgreen")
        p.show()
        _, outcome, _ = self.run_ticket(SUCCESS[0])
        print(f"  {SUCCESS[0]} again  →  {c.action_badge(outcome.action)}  ({outcome.rule})")
        self.check(outcome, "answered")

    def step_resilience(self):
        """Every provider failure degrades instead of crashing (A11)."""
        base = self.pipeline
        p = c.Panel("Induced provider failures  (FAULT_MODE)", "bcyan")
        p.text("Each mode wraps the model in a fault injector behind the real retry, backoff "
               "and circuit-breaker layer. The ticket must still be decided, never dropped.", "grey")
        p.divider()
        for mode in ("outage", "timeout", "ratelimit", "malformed"):
            provider = ResilientProvider(
                FaultInjector(StubProvider(), mode=mode),
                cache=Cache(WORK / f"cache_{mode}.json"), max_retries=2,
                sleep=lambda s: time.sleep(min(s, 0.15)),
            )
            pipe = Pipeline(config=self.config, control=self.control, metrics=Metrics(),
                            classifier=base.classifier, retriever=base.retriever, provider=provider)
            _, outcome, _ = self.run_ticket(SUCCESS[0], pipeline=pipe)
            crashed = c.style("ERROR", "bred") if outcome.error else c.style("no crash", "bgreen")
            degraded = c.style("degraded → extractive fallback", "byellow") if outcome.degraded else "not degraded"
            p.row(mode, f"{c.action_badge(outcome.action)}  {crashed}  ·  {degraded}")
            if outcome.error:
                self.mismatches += 1
        p.show()

    def step_full_run(self):
        """The unattended run over the whole evaluation set (A9, A10)."""
        source = DEVELOPMENT if self.args.full_dataset == "development" else VALIDATION
        out = ROOT / "artifacts/demo-run"
        cmd = [sys.executable, "-m", "evaluation.harness", "--input", source,
               "--output", str(out.relative_to(ROOT)), "--db", str((out / "decisions.db").relative_to(ROOT))]
        if self.args.backend:
            cmd += ["--backend", self.args.backend]
        p = c.Panel("Unattended run", "bcyan")
        p.row("Command", " ".join(["python"] + cmd[1:]), "bold")
        p.text("The same command a grader runs against the hidden set. No prompts, no manual "
               "steps; it ends by reconciling the decision log against the input.", "grey")
        p.show()
        env = dict(os.environ, HF_HUB_DISABLE_PROGRESS_BARS="1", TOKENIZERS_PARALLELISM="false",
                   TRANSFORMERS_VERBOSITY="error", PYTHONWARNINGS="ignore", PYTHONIOENCODING="utf-8")
        started = time.perf_counter()
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            # Embedding progress bars redraw with carriage returns, which text
            # mode splits into empty lines; those and model-loading chatter go.
            if (not line.strip() or "Batches:" in line or "HF_TOKEN" in line
                    or "huggingface" in line.lower() or "sentence_transformers" in line):
                continue
            print(c.style("  │ ", "grey") + line.rstrip())
        code = proc.wait()
        elapsed = time.perf_counter() - started
        metrics_path = out / "metrics.json"
        if code != 0 or not metrics_path.exists():
            print(c.style(f"\n  ✖ Harness exited with code {code}.", "bold", "bred"))
            self.mismatches += 1
            return
        m = json.loads(metrics_path.read_text(encoding="utf-8"))
        v, g, t = m["volume"], m["governance"], m["technical"]
        routing = t.get("routing") or {}
        processing = (m.get("run") or {}).get("wall_clock_seconds")
        timing = (f"processed in {processing:.1f}s  ({elapsed:.0f}s including model load)"
                  if processing is not None else f"in {elapsed:.1f}s")
        p = c.Panel(f"Result: {v['tickets_processed']} tickets {timing}", "bgreen")
        p.row("Answered", f"{v['answered_automatically']:>4}", "bgreen")
        p.row("Escalated", f"{v['escalated']:>4}", "byellow")
        p.row("Blocked", f"{v['blocked_by_guardrails']:>4}", "bred")
        p.row("Errors", f"{v['pipeline_errors']:>4}", "bgreen" if not v["pipeline_errors"] else "bred")
        p.divider()
        ok = g.get("log_reconciles")
        p.row("Decision log", f"{g['decisions_logged']} logged for {g['tickets_processed']} tickets  →  "
                              + ("RECONCILES" if ok else "DOES NOT RECONCILE"), "bold", "bgreen" if ok else "bred")
        r = t.get("retrieval") or {}
        if r.get("hit_rate_pct") is not None:
            p.row("Retrieval", f"hit rate {r['hit_rate_pct']}%  ·  correct abstention {r['correct_abstention_pct']}%")
        if routing:
            p.row("Routing", f"agrees with the expert {routing['agreement_pct']}%  ·  over-answered "
                             f"{routing['over_answered']}  ·  under-answered {routing['under_answered']}")
            p.row("Must-not", f"{routing['must_not_auto_respond_violations']} must-not-auto-respond tickets released",
                  "bgreen" if not routing["must_not_auto_respond_violations"] else "bred")
        p.row("Written", f"{out.relative_to(ROOT)}/results.json, metrics.json, metrics.md", "grey")
        p.show()


STEPS = [
    ("Environment check", "step_environment", "What is installed, and confirmation that hybrid retrieval is live."),
    ("A success", "step_success", "A ticket answered automatically from the documentation, with citations."),
    ("Escalations", "step_escalations", "Three different rules sending a ticket to a person, each with its reason."),
    ("A guardrail blocking", "step_guardrail", "A draft that invents a refund is withheld, then the real model's blocks."),
    ("Operator pause", "step_pause", "Pause automatic replies, watch the same ticket escalate, resume."),
    ("Failure handling", "step_resilience", "Outage, timeout, rate limit and malformed replies: degrade, never crash."),
    ("Full unattended run", "step_full_run", "The grader's command over the whole validation set, reconciled."),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the recorded demonstration.")
    ap.add_argument("--step", type=int, action="append", help="run only this step (repeatable)")
    ap.add_argument("--list", action="store_true", help="list the steps and exit")
    ap.add_argument("--no-pause", action="store_true", help="do not wait for Enter between steps")
    ap.add_argument("--backend", choices=("hybrid", "lexical", "chroma"), default=None)
    ap.add_argument("--reply-lines", type=int, default=8,
                    help="lines of each reply to show before truncating (0 shows all)")
    ap.add_argument("--full-dataset", choices=("validation", "development"), default="validation",
                    help="which set step 6 runs over (validation is 80 tickets, development 500)")
    args = ap.parse_args(argv)

    if args.list:
        for i, (title, _, what) in enumerate(STEPS):
            print(f"  {c.style(str(i), 'bold', 'bcyan')}  {c.style(title, 'bold'):<28}  {what}")
        return 0

    chosen = args.step if args.step else list(range(len(STEPS)))
    bad = [s for s in chosen if not 0 <= s < len(STEPS)]
    if bad:
        ap.error(f"no such step: {bad}; steps are 0 to {len(STEPS) - 1}")

    demo = Demo(args)
    c.banner("CloudServe support triage — live demonstration",
             "Real tickets, real pipeline, real guardrails. Offline: nothing leaves this machine.")
    try:
        for n, i in enumerate(chosen):
            title, method, what = STEPS[i]
            if n:
                c.pause(not args.no_pause, f"Next: step {i}, {title.lower()}")
            print()
            print(c.rule(f"Step {i} · {title}", "bcyan"))
            print(c.style(f"  {what}", "grey"))
            getattr(demo, method)()
    except KeyboardInterrupt:
        print(c.style("\n  Interrupted.", "grey"))
        return 130
    finally:
        demo.control.enable()
        if demo._log is not None:
            demo._log.close()

    print()
    if demo.mismatches:
        print(c.style(f"  ⚠ {demo.mismatches} outcome(s) differed from the rehearsal. "
                      "See the warnings above.", "bold", "byellow"))
        return 1
    print(c.style("  ✓ Every step produced its rehearsed outcome.", "bold", "bgreen"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
