# Recording the demonstration

Everything needed to record the video's live demonstration: four tools, how to
set them up on Windows, a check to run before pressing record, and a run of
show matched to the Submission Guide's structure.

Commands below are for PowerShell on Windows. On macOS or Linux use
`.venv/bin/python` wherever you see `.\.venv\Scripts\python.exe`.

## The four tools

| Tool | Command | Shows |
|---|---|---|
| **Demo runner** | `python scripts/demo.py` | Every required moment in order: a success, escalations, a guardrail blocking, the operator pause, failure handling, the full unattended run |
| **Demo page** | `python -m src.api --backend hybrid --open` | One ticket at a time in the browser, at `http://127.0.0.1:8000/demo` |
| **Test report** | `python scripts/test_report.py --open` | All tests grouped by acceptance criterion, as sentences, with skip reasons |
| **Live dashboard** | `docker compose -f monitoring/docker-compose.yml up -d` plus `python scripts/demo_traffic.py` | Grafana filling with live traffic, and the R-07 semantic-retrieval alarm |

All four run offline by default. Nothing leaves the machine unless you pass
`--use-provider` to the API.

## One-time setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Then warm up once, before the recording day.** The first run downloads the
MiniLM embedding model (about 90 MB) and builds the Chroma index. Do that now,
not on camera:

```powershell
.\.venv\Scripts\python.exe scripts\demo.py --no-pause
```

It should end with `✓ Every step produced its rehearsed outcome.` Once the
model is on disk, the tools stop checking HuggingFace for updates, which removes
the anonymous-download warning and most of the startup delay.

For the dashboard only, install **Docker Desktop**. It's optional. Without
Docker, see [the dashboard without Docker](#the-dashboard-without-docker).

## Before pressing record

1. **Terminal:** Windows Terminal, font size 16–18, window at least 100
   columns wide. The panels size themselves to the window up to 100 columns.
2. **Focus assist on**, notifications off.
3. **Check the configuration is the audited one:**
   ```powershell
   .\.venv\Scripts\python.exe scripts\demo.py --step 0
   ```
   `Semantic half` must say **available**. If it says NOT AVAILABLE, you'd be
   demoing the lexical fallback, which the fairness audit fails.
4. **Make sure the real system isn't paused:**
   `.\.venv\Scripts\python.exe -m src.control status` should say ENABLED.
   The demo's own pause step uses a separate switch and can't change this one.
5. **Browser zoom** about 125% on the demo page, so text is legible after
   video compression.
6. **Do one silent rehearsal** with `--no-pause` and watch for any ⚠ line.

## Run of show

The Submission Guide puts the demonstration at minutes 7 to 14 and requires at
least seven minutes of it. It must show **a success, an escalation, a guardrail
firing, and the full unattended run**. `scripts/demo.py` covers all four, in
order, pausing for Enter between steps so you set the pace.

| Time | Step | What to say |
|---|---|---|
| 7:00 | **0 · Environment** | "This is the full stack: hybrid retrieval with its semantic half live, running offline. Nothing leaves this machine." |
| 7:45 | **1 · A success** (VAL-0006) | Walk through the decision panel top to bottom: what it read, why it answered, which documents it retrieved and which it cited, that the guardrails passed, and that it was logged. Then the recorded live-model reply: "Same retrieval, same guardrails. With the model on, this is what the customer receives." |
| 9:00 | **2 · Escalations** | Three rules, three reasons. VAL-0043: a security incident always goes to a person. VAL-0001: an exposed key triggers sensitive-language review. VAL-0029: nothing in the documentation supports an answer, so it declines to guess. "Each escalation arrives with the draft and the sources attached, so the agent starts with context." |
| 10:30 | **3 · A guardrail blocking** (VAL-0010) | First the honest answer passes. Then **say plainly that the next draft is scripted**: "Offline generation copies from the documentation, so it can't misbehave. To show the guardrail, I'm handing the real validator a draft that invents a fee and promises a refund." It's blocked on two guardrails and nothing is released. Then the evidence panel: "And this isn't hypothetical. In the final live run the real model produced two drafts like this, including on this very ticket, and both were withheld." |
| 12:00 | **4 · Operator pause** | The same ticket from step 1 escalates while paused and answers again after resuming. "The switch is a file on this machine. There's deliberately no remote endpoint for it." |
| 12:45 | **5 · Failure handling** | Four induced failure modes, four decided tickets, no crash. "Every provider failure degrades to the extractive answer and is marked as degraded." |
| 13:15 | **6 · Full unattended run** | "The grader's exact command, over all 80 validation tickets." Point at the reconciliation line: "80 logged for 80 tickets. Nothing dropped." |

That's about six and a half minutes of terminal. To reach seven or more, add
one of these:

- **Demo page (1–2 min):** start the API, click two rehearsed tickets, and point
  at the decision log filling in at the bottom. Good for a non-technical
  audience.
- **Test report (1 min):** open `artifacts/test_report.html` and scroll the
  groups. It fits well at the start of "What the numbers say".
- **Dashboard (1–2 min):** see below. It's the most visual, and the most that
  can go wrong live.

Useful flags: `--step 3` runs a single step, `--list` shows all steps,
`--reply-lines 0` shows each reply in full, and `--full-dataset development`
runs step 6 over all 500 development tickets.

## Things to say, so nothing is overstated

These are true, and an assessor will notice if they're left unsaid:

- **Step 3's draft is scripted.** It says so on screen. Say it aloud too.
- **Step 1's live reply is a recording** from `evaluation/final/validation-live`,
  not a live call.
- **Offline replies read like document extracts.** They're safe but not polished.
  The live model writes the prose.
- **Validation isn't a clean held-out set.** 45 of its 80 tickets are exact
  duplicates of development tickets, and 69 are near-duplicates, which is why
  intent accuracy reads 100% there. The honest figure is the grouped
  cross-validation accuracy, **94.4%**.
- **The suite is 84 test runs, 81 of them distinct tests.** Three pause-control
  tests run twice, because the API test class inherits them. The report labels
  them.

## The live dashboard

Needs Docker Desktop, and three terminals:

```powershell
# terminal 1: the API, which Prometheus scrapes on port 8000
.\.venv\Scripts\python.exe -m src.api --backend hybrid

# terminal 2: Prometheus and Grafana, pre-wired
docker compose -f monitoring/docker-compose.yml up -d

# terminal 3: steady traffic, so the panels have something to show
.\.venv\Scripts\python.exe scripts\demo_traffic.py --rate 1
```

Open **http://localhost:3000**. Grafana goes straight to the CloudServe
dashboard with no login, scraping every 5 seconds and refreshing every 5, over a
15-minute window. **Start the traffic about two minutes before you show it**,
because rate panels need a couple of minutes of data. Prometheus itself is at
http://localhost:9090 (Status → Targets should show the API as UP).

What to point at:

- **Retrieval: semantic half** reads HYBRID in green. That's the R-07 alarm,
  and it's the only critical alert. Stop the API and restart it with
  `--backend lexical` and it turns red within seconds, which is a strong live
  moment if you want one.
- **Tickets per hour by outcome and by channel.**
- **Escalations by rule**: why tickets reach a person.

**The "Guardrail blocks" panel says No data offline, and that's correct.**
Offline generation never trips a guardrail, and Prometheus doesn't show a
labelled counter until it first increments. It fills only under
`--use-provider` traffic. Say so rather than letting it look broken.

Stop the stack afterwards with
`docker compose -f monitoring/docker-compose.yml down`.

### The dashboard without Docker

Run Prometheus natively with the checked-in `monitoring/prometheus.yml` (it
scrapes `localhost:8000`), then install Grafana, add a Prometheus data source
at `http://localhost:9090`, and use Dashboards → Import to upload
`monitoring/grafana_dashboard.json`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Semantic half NOT AVAILABLE` | Extras not installed, or the model never downloaded | `pip install -r requirements.txt`, then one run with internet access |
| A ⚠ "Rehearsed outcome was…" line | Usually the lexical fallback; otherwise a code change moved an outcome | Check step 0 first. CI runs this rehearsal on every push. |
| Startup takes about 15 s | torch and the embedding model loading | Normal. It happens during step 0 while you talk. |
| Garbled box characters | Old console host, or a font without box-drawing glyphs | Use Windows Terminal with Cascadia Mono or Consolas |
| No colour | Output is redirected, or `NO_COLOR` is set | Run directly in the terminal |
| Grafana panels are "No data" | No traffic yet, or Prometheus can't reach the API | Run `demo_traffic.py`; check http://localhost:9090/targets |
| API says address in use | Something else is on port 8000 | `--port 8001`, and change the target in `monitoring/docker/prometheus.yml` to match |

## Known issues visible on camera

These are in the system rather than the tooling. They're not fixed here, because
each one changes production behaviour or evaluated outputs:

- **The reason text says "100% confidence."** `src/route.py` formats a
  calibrated 0.9999 with `:.0%`, which rounds it up and contradicts A3's "never
  states certainty". The demo's own panels show two decimals (99.99%), but the
  reason text still says 100%.
- **Paused tickets are logged as `intent=unclear_request, confidence=0.0`.**
  That's a placeholder, but it inflates unclear-request counts in any audit of
  the decision log.
- **Extractive replies include markdown headers** such as `# Title` and
  `**Applies to:**`, pasted straight from the documentation.
