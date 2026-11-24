---
name: setup
description: Set up ApplyAI for a new user, or redo parts of it - asks what jobs and places to apply for, imports their existing CV, pulls extra career facts from their earlier Claude conversations (with consent), and writes the profile and search config. Use when the user wants to start with ApplyAI, says "set up my job search", changes what they are looking for, or has no ApplyAI workspace yet.
argument-hint: "[path to your CV]"
allowed-tools: Bash(applyai *), Bash(open *), Bash(xdg-open *), Read, Write, Edit, Glob, AskUserQuestion
---

# ApplyAI setup

You are setting up ApplyAI, a job-search tool that scans job boards every day, screens postings
against the user's profile and writes a tailored one-page CV and cover letter for each good fit.
Everything it writes is backed by one file, `profile/facts.md`. A fact that isn't there never
appears in an application. Your job is to fill that profile honestly and completely, and to
configure the search.

The CLI is `applyai` (the plugin puts it on PATH; if the shell can't find it, use
`${CLAUDE_PLUGIN_ROOT}/bin/applyai`). The first call builds its Python environment, which
takes about a minute.

Work through the steps in order. Keep the user informed in one or two lines per step. Ask with
AskUserQuestion when there are clear options. Ask in plain text when the answer is open. Never
ask for something the CV or an earlier answer already told you.

If the user passed a CV path as an argument (`$ARGUMENTS`), use it in step 3 without asking again.

## 1. Check the machine

Run `applyai doctor`. For each ✗, give the user the install command it prints and offer to run
it. Installing software needs their yes. Without `pdflatex` and `pdftotext` nothing works.
`npx` is only needed for the browser agent. If the Claude login check fails inside this
session, ignore it here: interactive use works, and unattended runs are handled in step 7.

## 2. Workspace

All user data lives in one folder, outside the plugin. Ask where. The default is
`~/Job Applications`. On macOS, warn that folders inside Desktop, Documents or Downloads can't
run the daily background job. Then run `applyai init "<path>"`. If a workspace already exists
(`applyai where`, then check that its config.yaml exists), read its config.yaml and profile
first. Ask what they want to change instead of starting over.

## 3. What they are looking for

First get the CV, because it answers half of the questions. Ask for the file path. PDF, Word,
LaTeX, Markdown or a LinkedIn "Save to PDF" export all work. Run `applyai import-cv "<file>"`
and read the text carefully.

Then ask, at most two AskUserQuestion rounds plus free-text follow-ups:
- **Roles and fields**: what job titles and areas, and what they want to do more or less of.
  Offer the 3 likeliest directions from the CV as options.
- **Level**: early career / mid / senior; also PhD positions, internships or working-student
  roles if relevant.
- **Where**: cities (several are fine), remote, willingness to relocate.
- **Constraints**: work authorization and visa needs per country, languages they speak and
  how well, start date and notice period, deal-breakers (industries, company size, travel,
  defense, ...).
- **Companies**: dream employers or companies they already know they want.
- **Language**: language of the CV; which languages they can write cover letters in.
- **Salary expectation** per country (for application forms; optional).

## 4. Context from earlier Claude conversations (ask first)

Many people have discussed their projects, theses and jobs with Claude. Those details are often
missing from the CV. Explain in two sentences what this step reads: local Claude Code session
files and memory notes on this computer, plus a claude.ai export if they give you one. Nothing
is uploaded, and nothing goes into the profile unless they confirm it. Then ask with options:
- "Yes, search my Claude Code history" → `applyai context --terms "<employers, schools, project names, key skills from the CV>"`
- "Also a claude.ai export": tell them how to get one: claude.ai → Settings → Privacy →
  Export data. They get an email with a download link that works for 24 hours. Then add
  `--export "<zip or folder>"`.
- "Skip"

The command prints the path of a Markdown digest. Read it. Also look at what you yourself
remember about the user in this conversation's context, if anything. Pull out career facts
that the CV lacks or states more weakly: projects, tools, results with numbers, publications,
talks, awards, side projects, what they built at each job, and their own descriptions of what
they enjoy. Ignore anything about other people, health, finances, relationships or anything
else that has no place in an application.

Show the candidates as a numbered list with the source of each ("from the session in
~/code/robot-arm, Oct 2026"). Ask which to keep and whether any numbers need correcting. Only
confirmed items go into facts.md. If the digest is empty or the user skipped this step, ask
them instead: "What did you build or achieve recently that isn't on your CV?"

## 5. Write the profile

Look at the examples in `${CLAUDE_PLUGIN_ROOT}/applyai/examples/profile/` for the format of each file.
Write into `<workspace>/profile/`:

- **facts.md**: everything true and relevant. Use one section per job / education / project
  with dates and location. List every concrete result and number, and the skills under "Skills
  (only these)". Be generous here. The CV is chosen from it per job, so facts.md can be much
  longer than one page. Contact and logistics go at the top (citizenship, visa needs,
  languages with level, availability).
- **cv_base.tex**: their CV in the `resume.cls` format of the example (`rSection`,
  `rSubsection{Org}{Mon YYYY -- Mon YYYY}{Title}{City, Country}`, `\item` bullets, `\rEntry`
  for an entry without bullets, a Skills `tabular` with `Category & items \\` rows). Keep their
  content and order. Use 3-letter months. It must be exactly one page and fill it. Run
  `applyai compile-base` and iterate until it reports `"pages": 1`, `page_fill` ≥ 0.93 and no
  `one_word_last_lines`. Then open `profile/cv_base.pdf` for them (`open` on macOS, `xdg-open`
  on Linux) and ask if anything is wrong. If they have their own LaTeX design: their class can
  replace the template as `profile/resume.cls`, but only if it defines the same environments.
  Otherwise use the template.
- **cv_rules.md**: their personal CV rules, from what they told you (entries that must stay,
  what to never show, wording preferences). Short plain sentences.
- **letter_examples.md**: ask for 1-3 cover letters they wrote and liked (file paths or pasted
  text). If they have none, leave the file out. ApplyAI then writes in a neutral, direct voice.
- **answers.md**: the form answers from step 3 (salary, start date, work authorization,
  relocation, links, EEO = decline). Leave out anything they did not give you.
- Optional files: `photo.jpg` (CV photo, common in Germany, Switzerland and Austria),
  `signature.png` (for the letter), certificates. Ask once and copy what they give you.

Then write **`<workspace>/config.yaml`**, starting from the commented one `applyai init` put
there:
- `me`: name, email, phones, address lines for the letter, `city` (and `city_de` for German
  letters).
- `candidate.summary`: one dense paragraph (field, level, strongest skills, what they want).
  `candidate.screening_rules`: their constraints and deal-breakers in plain words.
  `availability`, `willing_to_relocate`.
- `locations`: one entry per city with `country`. Add `aliases` for the suburbs and nearby towns
  where employers sit (for example Garching for Munich, or Mountain View for the Bay Area).
  Munich, Zurich, SF Bay Area, Berlin, London, New York, Boston, Seattle, Paris and Amsterdam
  already have built-in lists. Set `remote: true` if they want remote jobs.
- `queries`: 8-20 search terms as people title such jobs, in English, plus `queries_local`
  for local-language boards (e.g. German terms for StepStone).
- `title_include` / `title_exclude`: case-insensitive regexes. Exclude seniority levels and
  role types they don't want.
- `relevance_terms`: 10-25 `regex: weight` pairs (weight 1-5) for their field.
- `tailoring`: `cv_language`, `letter_languages`, `must_keep` (entries from cv_rules that must
  stay), `ensure_skills`, photo settings (photo only for DACH countries, if they gave one).
- `company_boards`: take their dream companies plus 15-30 employers in their field and cities
  that you know of. For each, run `applyai find-board "<Company>" --website <url if known>`.
  Add every hit as `{name, ats, token}`. For companies without a public board, offer to add
  their careers page as a `browser_targets` entry and turn on `sources.browser_agent`. It runs
  in a headless browser, so no windows pop up.
- `sources`: company_boards on. Turn on `jobs_ch` for Swiss cities and `networks` for YC
  companies in their cities (and CDTM / Manage&More for Munich). **LinkedIn and StepStone**
  find the most postings, but their terms forbid automated access. Explain that in one sentence
  and let the user decide. Leave them off unless the user says yes.

## 6. Test run

Run `applyai scan --only company_boards` (a few minutes, no folders written) and show the
shortlist. If it is empty or full of bad fits, adjust queries, title filters, relevance terms or
`min_fit_score`, and say what you changed. Then make one real application for the best hit:
`applyai pending --limit 1` (2-4 minutes). Open the folder, show the CV and letter PDFs, and ask
for feedback. Turn their corrections into facts.md / cv_rules.md changes. Don't hand-edit the
generated files.

## 7. Daily runs (optional)

Offer a daily run: `applyai schedule 07:30` (macOS launchd / Linux cron). Unattended runs need
their own Claude login. The user does that part themselves, because it involves a secret
token: they run `claude setup-token` in a terminal, then `applyai save-token` and paste it.
Don't ask them to paste the token into this chat. Check with `applyai doctor` afterwards.
Without a schedule they can just run `/applyai:run`.

## Finish

Close with a short summary: workspace path, what the profile contains (counts: jobs, projects,
skills), cities, number of company boards and which sources are on. Then the next commands:
`/applyai:run` (find and tailor), `/applyai:apply` (send, with their approval for each), and
`/applyai:track` (status and outcomes).

## Rules

- Truth over polish. Never add a fact, number or skill the user didn't give or confirm. When
  the CV and a conversation disagree, ask.
- Keep the profile in the user's workspace only. Don't send its contents anywhere other than
  the Claude calls ApplyAI makes.
- Don't sign up for anything, don't accept terms, and don't run installers without the user's
  yes.
