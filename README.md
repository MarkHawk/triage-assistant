# triage-assistant

A support-triage assistant for single sign-on problems. You give it a support ticket (and, if you have them, log lines); it finds the matching runbook, names the most likely cause with the evidence that supports it, drafts the reply to the customer, and says plainly when it does not know enough yet. It runs on a local model through Ollama, or on Claude, and **everything a model returns is checked in code before anyone sees it**.

It works against identity-lab (a separate repository), a lab of single sign-on services that can be broken on purpose, each failure with a support runbook. The tickets come from the fictional company Northgate Supplies.

Built with AI-assisted tooling (Claude Code).

## The 60-second demo

No API key, no GPU and no Docker. Demo mode replays answers recorded from real model runs. It needs a checkout of identity-lab beside this one, because it reads the runbooks.

```bash
# with identity-lab checked out next to this repository
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
BACKEND=demo python -m triage ticket demo/fixtures/redirect-loop/ticket.md --logs demo/fixtures/redirect-loop/logs.txt
BACKEND=demo python -m triage ticket demo/fixtures/printer-offline.md          # too vague: it asks, and asserts nothing
BACKEND=demo python -m triage ticket --text "I'm locked out, can you unlock me please?"   # an account request, not a fault
```

The first one, a real redirect loop broken in the lab and recorded from `qwen2.5:14b`, prints (shortened):

```
## Likely scenario: redirect-loop
Confidence: **high**

### Likely cause
A header line added to the proxy's configuration for this app made the proxy stop telling the app that
browsers connect over HTTPS, so the app, which only accepts HTTPS, redirects every request to the HTTPS
address the browser is already on.

### Evidence
- `2026-10-26 22:12:44,141 INFO oidc-rp event=force_https path=/ scheme=http x_forwarded_proto=-`
  The app thinks the request was plain HTTP, as indicated by `scheme=http` and `x_forwarded_proto=-`.

### Runbook
runbooks/15-redirect-loop.md

### Fix steps
1. Make the location send the forwarded headers again by either taking the extra line out or repeating
   every header the location needs.
...
### Draft reply to the customer
...
Sign-in service support
```

## Scores

<!-- scores:start -->
Latest score for each evaluation, backend and model. Window 12288 throughout.

| Evaluation | Backend and model | Date | Result |
|---|---|---|---|
| Real tickets: 20 written by the author (`tests/real_tickets.yaml`), ticket text only | Ollama `qwen2.5:14b` | 2026-10-27 | retrieval right 20/20, mode right 19/20, cause right 15/15 of those diagnosed, answers breaking a reply rule 0/19 |
| Lab scenarios: 7 cases broken in the lab, scoped logs, 3 runs each | Ollama `qwen2.5:14b` | 2026-10-26 | scenario right 21/21 |
| Lab scenarios: the same | Ollama `qwen2.5-coder:14b` | 2026-10-26 | scenario right 12/21 |
| Retrieval cold set: 24 queries, no model | none | 2026-10-26 | 23/24 |
| Any of the above | Claude | | not run |

**The rules are enforced in code, not trusted to the model.** On the real-ticket set (run D) the model needed a second attempt on 10 of 19 answers, and code corrected 7 of them; every one is flagged to the engineer above the answer.

**Read these with care.** The real-ticket score was reached after changing the code in response to reviewing those same tickets, so it is not a held-out score ([docs/post-review-changes.md](docs/post-review-changes.md) has all four runs side by side). The lab-scenario runs predate those changes ([docs/live-runs-ollama.md](docs/live-runs-ollama.md)). Only the cold set was run once and never tuned on.
<!-- scores:end -->

## Status

Built: ticket mode with Ollama, Claude and demo backends, plain-language runbook retrieval, the secrets guard, the reply rules, and a real-ticket evaluation. **Not built yet:** watch mode (following the lab's logs as failures happen) and the break-it loop evaluation. The Ollama backend has been run live on two Qwen 2.5 14B models; **the Claude backend has only been tested against a fake client**, never against the real API.

## How to run it

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # then fill in what you use; .env is gitignored
python -m pytest
```

`LAB_DIR` in `.env` (or the environment) points at identity-lab (default `../identity-lab`). The lab is only ever read. Tested with Python 3.14. Without the lab, the tests that need its runbooks are skipped.

- **Local model:** set `BACKEND=ollama`, `OLLAMA_HOST` and `OLLAMA_MODEL` (`qwen2.5:14b` is recommended; see [Local models](#local-models)). Ollama can run on another machine on your network: [docs/ollama-on-another-machine.md](docs/ollama-on-another-machine.md).
- **Claude:** set `BACKEND=claude` and `CLAUDE_API_KEY`.
- **Demo:** `BACKEND=demo`, the default, needs neither.

Output is Markdown in the terminal. There is no web page: the lab already uses every port from 8180 to 8189.

## Ticket mode

```bash
python -m triage ticket demo/fixtures/redirect-loop/ticket.md      # a ticket file (see triage/tickets.py)
python -m triage ticket --text "The portal goes round in circles"
python -m triage ticket my-ticket.md --logs lab.log --backend ollama
python -m triage ticket my-ticket.md --backend claude --record     # also save the answer for demo mode
```

A ticket file is the customer's message, with an optional `## Log excerpt` section. Only its subject and body reach a model, never the other header fields.

**Two modes, chosen by how well the runbooks match:**

- **Diagnose** (at least one *strong* match): the answer may state the likely cause, with the log lines that prove it quoted word for word, the matching runbook, fix steps and a draft reply.
- **Clarify** (only weak matches, or none): the answer **asserts no cause**. It asks 3 to 6 questions drawn from the weak runbooks' symptoms and "Questions I'd ask the customer", and mentions those runbooks only as conditions ("if the symptoms match ..., runbook 15 may apply"). The code checks this: an answer with a cause, a fix, a confidence above low or "the cause is ..." wording is sent back once with the reasons, then replaced by an answer built by code. With the demo backend, clarify answers are built by code and no model is used.

Whatever a model returns is checked before you see it: quoted evidence that is not in the supplied text is dropped (quote marks and spacing do not count as differences, and a quote taken from a runbook is labelled as such), the runbook is derived from the outcome rather than taken from the model, and confidence is capped when the evidence is thin.

**Demo mode** replays diagnose answers from `demo/answers/`, saved by `--record` after a real run and keyed by the ticket, the logs and the retrieved runbooks, so changing the prompt does not orphan them. Four are saved, with their fixtures in `demo/fixtures/`.

**The demo recordings are single runs, not averages.** Each case was run on `qwen2.5:14b` at window 12288, after the post-review rules below, and the first run that passed every rule at its first attempt with nothing corrected was recorded. Each recording's metadata names the model and date.

| Case | Recorded from | Note |
|---|---|---|
| `redirect-loop` (diagnose) | `qwen2.5:14b` | |
| `expired-signing-cert` (diagnose) | `qwen2.5:14b` | |
| `cors-blocked` (diagnose) | `qwen2.5:14b` | quotes only the runbook's own console message, never the log line, so it has no verified evidence and stays at medium; the engineer section labels the quote as the runbook's example |
| printer offline (clarify) | `qwen2.5:14b` | |

Cases where a model got the scenario wrong have no recording. [docs/live-runs-ollama.md](docs/live-runs-ollama.md) has the earlier 42-run comparison of two models.

**Claude** uses structured output (not a forced tool choice, which Claude Sonnet 5.5 rejects). If the API declines a request it can re-run it on a fallback model (`CLAUDE_REFUSAL_FALLBACK=1`, the default).

## What the assistant will and will not say

These rules are checked in code on every model answer. The model is told once when it breaks one; if it breaks it again, code corrects the answer and says so above it.

- It never asks the customer for a password, shared secret, key, token or cookie, or for a screenshot of the Network tab, a HAR file or request headers. It asks for the console error text and the status code instead.
- `SUPPORT_PERSONA=vendor` (the default) treats what the ticket calls "your ..." (your API, your RADIUS server), and anything in `VENDOR_OWNS` (sign-in service, identity provider, API), as ours, so the reply says what we will check or fix. What the ticket calls "our ..." is the customer's, so "please check" or "we recommend", never "we will". `internal` may promise work.
- Every reply ends with the same sign-off, whichever mode or source made it: "Sign-in service support" in vendor mode (`SUPPORT_SIGNOFF` overrides it).
- The cause line must describe the runbook it diagnoses, compared with that runbook's own one-sentence root cause. If it does not, the runbook's statement is used and confidence is capped at medium.
- Below high confidence the reply says "the most likely cause is" and asks a confirming question about something the ticket does not already say. High confidence needs a quoted log line, or a ticket that quotes the runbook's documented error text word for word, and a cause line that matches the runbook.
- Nothing specific to the lab appears in fix steps or replies, fix steps are actions, not questions, and nothing is copied from the prompt's worked example.
- "Not a lab fault" is not accepted when a runbook matched strongly and nothing points to the user's side.
- Account lockouts and admin requests get a short reply pointing to the account administrator, with no questions and no model.

These came from reviewing the real-ticket set. [docs/post-review-changes.md](docs/post-review-changes.md) lists each change with before-and-after scores.

## Known limitations

- **Ownership is read from wording.** When a ticket does not say whose something is, the reply can get it wrong. t09 of the real-ticket set says "Please check if a rate limit was recently added or changed on the proxy": that reverse proxy sits in front of our identity provider, so it is ours, but the ticket does not say so and "proxy" is not in `VENDOR_OWNS`. Adding it there would also claim proxies that belong to customers, such as an egress proxy, so it is left as it is. The redirect-loop demo above has the same gap ("We recommend checking the proxy configuration").
- **Code repairs read like templates.** For example, "Please check the shared secret on both the new controller and the RADIUS server" becomes "We will check the RADIUS server on our side, and we recommend that you check the new controller on yours." Correct, but mechanical; the flag above the answer says when code did it.
- **The model sometimes presents runbook text as evidence.** Such quotes are dropped, labelled "quoted the runbook's own example, not the ticket or logs", and never count towards confidence. In run D it happened four times.
- **Vague whole-organisation outages are genuinely ambiguous.** "Everybody got kicked out at about nine this morning and nobody touched anything" fits runbooks 01 (expired signing certificate), 02 (clock skew), 04 (directory bind) and 17 (maintenance left on) equally well, because none of them has an error text to tell them apart. Retrieval gets this wrong in the one cold-set miss: it returns only runbook 08 (rate limit), as a **weak** match. That is why a weak-only result never gets a stated cause: the assistant asks questions instead. The miss is recorded, not fixed.
- **Unrelated tickets can still get weak matches.** A laptop, printer or finance-group request returns weak keyword matches rather than nothing. They are labelled weak, and the answer then asserts nothing.

## Retrieval

Only the best two or three runbooks go to the model. `python -m triage retrieve "it goes round in circles"` shows what a ticket would retrieve and why; add `--logs file` to include log lines.

How it ranks, with no model and no network:

1. BM25 over the runbooks' sections. The "how customers describe it" lines count most, so the runbooks' own customer language is searched.
2. `data/phrases.yaml` maps everyday wording ("goes round in circles", "says something about a redirect") to runbooks. It also has hints for user-side causes (an old bookmark, a forgotten password), which stop keyword overlap alone from forcing a runbook.
3. The lab's own error text and log patterns (`SCENARIO_EXPECT`, `SCENARIO_LOG`) and `event=` names in log lines count as strong evidence.

A match is **strong** if it has any evidence beyond keywords, and **weak** otherwise. The prompt says which, and no match at all is a proper result.

### How well it works

`tests/retrieval_queries.yaml` has 53 plain-language queries (including unrelated and user-side tickets that should match nothing). Counting a query as right if the expected runbook is in the top three, or nothing is returned when nothing fits:

| Set | Queries | Plain keyword ranking | Phrases and evidence, first run | Now |
|---|---|---|---|---|
| dev (tuned on) | 20 | 17 | 20 | 20 |
| held-out | 19 | 12 | 17 | 19 |
| fresh | 14 | not run | 10 | 13 |

Be careful reading the last column. After the first run I fixed what the failures showed (a keyword floor, a word-coverage check, a better stemmer and a few phrases), so the held-out and fresh sets are no longer independent evidence; the "first run" column is the fair one. `tests/test_retrieval.py` has floors so retrieval can't quietly get worse.

### The cold set

`tests/cold_queries.yaml` holds 24 queries written without seeing the retriever, the phrases file or the tuned sets. It was run once with no tuning and scored **23 of 24** (19 fault queries, 18 correct; 5 that should match nothing, all correct as "nothing strong"). It is pinned by hash in `tests/test_cold_set.py`, which also holds a regression floor of 23.

**It stops being independent evidence the moment anything is tuned against it.** If that ever happens, say so here and stop quoting the score as held-out.

## Real-ticket evaluation

You write the tickets; the runner scores what the assistant does with them. The author's 20 tickets are in `tests/real_tickets.yaml` (all fictional).

1. Copy `tests/real_tickets.template.yaml` to a new file and write your tickets in it. For each, give the ticket text and the runbook a good engineer would open first (or `null` if none fits, such as a password reset). Use fictional names only.
2. Run it: `python -m evals.real_tickets --file <your file>` (defaults: Ollama, `qwen2.5:14b`, window 12288, and `tests/real_tickets.yaml`). Each ticket is triaged once, with **ticket text only and no logs**.
3. Open the report in `results/`. It gives the table below and then every answer in full, so you can judge the draft replies:

| Column | Meaning |
|---|---|
| Retrieval right | the expected runbook was in the top three retrieved (or, if none was expected, nothing matched strongly) |
| Mode appropriate | diagnose or clarify, against the mode you expected |
| Cause right | in diagnose mode, the scenario named belongs to the expected runbook. In clarify mode no cause is asserted, so it shows n/a |
| Send-ready | **left blank for you**: yes / with edits / no |

4. Fill in the last column, then `python -m evals.real_tickets --summarise results/<report>.md` counts your verdicts and breaks them down by whether retrieval, mode and cause were right.

**Do not tune retrieval, phrases or prompts against these tickets before the first review.** The ticket file's hash is recorded in each report so a later edit is visible.

## Safety

- A value from the lab's own `.env` is a hard refusal anywhere: nothing is sent to a model.
- Tokens, cookies and SAML responses pasted into a **ticket** are masked, flagged to the engineer, and triage carries on. The customer reply then advises against sending such values.
- Secrets in **log lines** are masked and flagged the same way by default, since the lab masks its own logs and a secret there means something slipped through. Set `LOG_SECRETS=refuse` to stop instead.
- Whatever the settings, the whole prompt is checked again just before it is sent.
- Ticket and log text is treated as data, never as instructions.

## Local models

For a model of about 14B parameters on a 16 GB GPU, `OLLAMA_NUM_CTX` is sent with every request, and the prompt is budgeted to fit it (`OLLAMA_NUM_CTX` minus `OLLAMA_RESERVE_OUTPUT_TOKENS`). Nothing is cut silently:

- The ticket, the log snapshot and each runbook extract are trimmed to the budget, error-like log lines first, and every cut is reported above the answer.
- Sizes are estimated on the cautious side, below what was measured on Qwen 2.5 14B: 3 characters a token for prose and runbook text, 1.4 for log lines, which tokenise nearly a digit at a time. The reply is also checked: if Ollama reports it read almost the whole window, that is an error, because it probably cut the prompt.
- An answer cut off by the length cap is an error too.
- The first request has its own, longer timeout (`OLLAMA_FIRST_TIMEOUT_S`) because it may load the model into the GPU.

The worst case is tested: three of the longest runbooks, a 60 KB log snapshot and a very long ticket, at windows of 8192, 12288 and 16384 tokens. It fits, but the window decides how much of each runbook the model sees (the runbooks run up to about 10,000 characters). The ceiling for one runbook extract grows with the window; the figures below are the worst case, with 60 KB of logs competing for the space, and a typical ticket sees more:

| `OLLAMA_NUM_CTX` | Each runbook extract, worst case | Prompt estimate |
|---|---|---|
| 8192 | about 2,100 to 2,400 characters | about 6,300 tokens |
| 12288 | about 3,800 to 4,500 characters | about 10,300 tokens |
| 16384 | about 6,700 to 7,200 characters | about 14,600 tokens |

Whether a given 14B model fits 12288 or 16384 on a 16 GB card depends on its quantisation, so check VRAM before raising it. On an RTX 5060 with 16 GB, `qwen2.5:14b` ran entirely on the GPU at all three. What a real run measured, including a correction to the token estimate, is in [docs/live-runs-ollama.md](docs/live-runs-ollama.md).

## Repository checks

`python -m pytest` includes a scan of every committed file for home paths, em dashes and addresses outside the lab's ranges. Add personal terms (your username, employer) one per line to `.private-terms`, which is gitignored, and the scan checks for those too.

## Names

The assistant's company, Northgate Supplies, and its system names live in `data/names.yaml`. Nothing depends on identity-lab using them.
