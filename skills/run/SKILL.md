---
name: run
description: Run ApplyAI's job search - scan job boards, screen postings against the user's profile and write tailored one-page CVs and cover letters for the best fits, or tailor one posting the user found. Use when the user asks to find jobs, run the job search, check for new postings, or tailor an application for a specific job link.
argument-hint: "[job URL | scan | pending | --limit N | --only sources]"
allowed-tools: Bash(applyai *), Bash(open *), Bash(xdg-open *), Read, Glob
---

# ApplyAI run

The CLI is `applyai` (if the shell can't find it: `${CLAUDE_PLUGIN_ROOT}/bin/applyai`). If
`applyai status` says there is no workspace or the profile is incomplete, stop and suggest
`/applyai:setup`.

Pick the command from what the user wants (`$ARGUMENTS`):

| User wants | Command | Time |
|---|---|---|
| new postings + applications (default) | `applyai run` | 20-40 min |
| only see what's new, write nothing | `applyai scan` | 5-15 min |
| tailor what was shortlisted earlier | `applyai pending` | ~3 min per application |
| one posting they found | `applyai tailor "<url>"` | 3-4 min |
| a posting behind a login | ask them to paste the text, save it to a file in the workspace, then `applyai tailor "<url>" --desc-file F --title T --company C --location "City, Country"` | 3-4 min |
| fewer or specific sources | add `--limit N` or `--only company_boards,networks` | |

Long commands: run them in the background and tell the user it is running and roughly how
long it takes. Don't poll. Wait for the completion notice.

When it finishes:
1. Read the end of the output (or the newest `*_run.log` in `<workspace>/.applyai/logs/`).
2. Read `<workspace>/_Overview.md` and list the new applications as a short table: company,
   position, city, fit, ATS score, rating. Add one line on why each fits (from its `Job.md`).
3. Mention anything that needs the user: postings that failed, sources that refused requests,
   ratings below 3.5, or a login error (then point to `applyai doctor`).
4. Offer the next step: look at a specific CV (open the PDF), the dashboard
   (`applyai dashboard --open`), or send them with `/applyai:apply`.

Never edit the generated CVs or letters by hand to "improve" them. If the user wants something
different, change `profile/facts.md`, `profile/cv_rules.md` or `config.yaml`, then re-run
`applyai rerender <folder>` (layout and rules) or `applyai tailor` (content).
