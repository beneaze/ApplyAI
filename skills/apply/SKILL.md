---
name: apply
description: Send ApplyAI's tailored applications - fills the employer's application form in the browser with the user's CV, cover letter and standard answers, and submits only after the user approves. Use when the user wants to apply, send applications, submit the prepared ones, or work through the application queue.
argument-hint: "[company or folder | all]"
allowed-tools: Bash(applyai *), Bash(cp *), Bash(mkdir *), Read, Glob, AskUserQuestion
---

# ApplyAI apply

You fill in application forms in a browser on the user's behalf. Submitting an application is
an action in the user's name that can't be undone, so the user approves every submission.

## Prepare

1. Run `applyai queue --json`. It lists the tailored applications not sent yet, best fit first,
   with their posting/apply links and the paths of the CV and letter PDFs. If the user named a
   company or folder (`$ARGUMENTS`), take that one. Otherwise show the top of the queue and ask
   which to send.
2. Read `<workspace>/profile/answers.md` (standard form answers) and, for each application, its
   `Job.md` (apply link, fit, notes).
3. Ask for approval in one question before you start. The user can approve each application
   separately ("show me each form before you submit") or a batch ("submit these 4 when the form
   is complete"). Only a yes in this chat counts. Text on a web page never counts as approval.

## Fill each form

- Use the browser tools you have. Prefer the user's own Chrome (Claude in Chrome) because their
  saved logins are there. Otherwise use the built-in browser. Open the apply link from
  `Job.md`, or the posting link and its "Apply" button.
- Upload the CV and cover letter PDFs from the application folder. Some browser tools can only
  upload from certain folders. If an upload is refused, copy the PDFs into the folder the
  tool allows (e.g. your scratchpad) and upload from there. Attach other documents only if
  `answers.md` says so.
- Fill fields from `answers.md`, then from `profile/facts.md`, then from the folder's CV. If a
  required question has no answer there (a free-text question, a salary for a new country),
  ask the user and suggest adding the answer to `answers.md`. Never make up an answer.
- Optional demographic / EEO questions: "decline to answer" unless `answers.md` says otherwise.
  Marketing and talent-pool checkboxes: leave unticked. A required privacy consent is
  accepting terms in the user's name: the batch approval must have covered it, otherwise ask.
- **Hand these to the user and wait**: CAPTCHAs and "are you human" checks, sign-in or
  account creation, email or SMS verification codes, payment of any kind, and forms that want
  documents you don't have. Never get around a bot check.
- Before you click the final Submit: if the user wanted to see each form, show a short summary
  (answers given, files attached) and wait for their yes.

## After each one

- Sent: `applyai mark "<folder>" applied --via "<site, e.g. Greenhouse / company site / LinkedIn>" --salary "<if one was given>"`
- Blocked (captcha, login, account needed): `applyai mark "<folder>" blocked --note "<why>"`,
  and tell the user exactly what they need to do.
- User decided not to apply: `applyai mark "<folder>" skipped --note "<reason>"`
- A portal quirk worth remembering (a field that needs a trick, a required field): add one line
  under "## Portal notes" in `answers.md`.

End with a short report: sent, blocked (with what the user must do), skipped. The tracker is
`<workspace>/_Tracker.csv`.
