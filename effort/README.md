# Effort tracking

Source of truth for the capstone Effort Log (`Capstone_Pack/04_Submission/Effort_Log.docx`).
Log here as you work; generate the DOCX tables at the end. Committing this directory daily
is what proves the log was kept throughout rather than written from memory — the template
says a backfilled log "is marked as such", and the submission checklist checks for entries
across all three weeks.

## Files

| File | What it is | Edit when |
|---|---|---|
| `planned_baseline.csv` | Planned hours per stage. **Freeze it.** | Once, before you start. Never again. |
| `effort_log.csv` | Daily task-level entries. Append only. | Twice a day |
| `sprint_estimates.csv` | Per-item estimates from the Stage 4 sprint plan | At Stage 4, then freeze |
| `eval_runs.csv` | Every validation / hidden-set run | Each run |
| `rollup.py` | Generates the DOCX tables + runs the checks | Never; just run it |

## The routine

Append two rows a day — end of morning, end of day. Two minutes.

    date,stage,task,hours,produced,blocked_by,sprint_item,ai_tools,ai_notes

- `stage` must be one of the six names in `planned_baseline.csv`, spelled identically.
  `rollup.py` flags any name it does not recognise, which is how typos get caught.
- `sprint_item` links a row to a Stage 4 backlog item. Only Section 6 needs it.
- `ai_tools` / `ai_notes` feed the report's AI-use declaration. Record where you
  **overrode or corrected** the tool, not just that you used it.
- Log the dead ends. Four hours into an approach you abandoned is a real cost and the
  template asks for it explicitly. These rows are the best material for Section 7 and for
  the report's reflection section.

Then, at the end of the day:

    git add effort/ && git commit -m "effort log: $(date +%F)"

## Weekly, and before you submit

    python3 effort/rollup.py

It prints Section 2, Section 6 and Section 8 ready to paste into the DOCX, and warns on:
unrecognised stage names, sprint items with no estimate, gaps longer than two days,
and build hours exceeding half the total.

Sections 1, 7 and 8 of the DOCX are written by hand. Section 7's four questions are worth
drafting in week two while the answers are still fresh.
