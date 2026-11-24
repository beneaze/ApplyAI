---
name: track
description: Show the state of the user's ApplyAI job search and record outcomes - how many postings were found, what waits to be sent, which applications were sent, and updates like interviews, offers or rejections. Use when the user asks about their applications, their job search status, or reports news from an employer ("I got an interview at X", "Y rejected me").
argument-hint: "[company] [interview | offer | rejected | withdrawn]"
allowed-tools: Bash(applyai *), Read
---

# ApplyAI track

The CLI is `applyai` (if the shell can't find it: `${CLAUDE_PLUGIN_ROOT}/bin/applyai`).

- **Dashboard**: `applyai dashboard --open` rebuilds and opens `<workspace>/_Dashboard.html`:
  sent / screened / interviews / response rate, postings and applications per week, the
  pipeline, posted salary ranges next to the user's ask, employer ratings, a strip of the last
  28 daily runs (failed runs show the reason), and a sortable table of every application with
  links to its CV, letter, Job.md and folder. It rebuilds itself after every run and `mark`.
- **Status**: run `applyai status`, then read `<workspace>/_Tracker.csv`. Report in a few lines:
  sent (by status), waiting to be sent, interviews and offers in progress, and how long ago
  the last run happened. If more than 2 days passed and no daily run is scheduled, mention
  `/applyai:run`.
- **An outcome** ("interview at Acme", "rejected by Beta"): find the application folder in
  `_Tracker.csv` (column Folder, relative to the workspace). If more than one matches, ask
  which. Then run `applyai mark "<workspace>/<folder>" <interview|offer|rejected|withdrawn>
  --note "<date or detail the user gave>"`. Confirm in one line.
- **An interview**: offer to prepare. Read the folder's `Job.md` (job description) and the CV
  and letter that were sent, then suggest likely questions and the facts from
  `profile/facts.md` that answer them best.
- **Patterns**: after 15 or more sent applications, you can offer a short look at what got
  replies (fields, company types, fit scores). Only say what the tracker shows; with few data
  points, say so.

The user may keep their own spreadsheet (e.g. a Google Sheet). If they have a connector for it
and ask, copy the rows from `_Tracker.csv` there. Don't do it on your own.
