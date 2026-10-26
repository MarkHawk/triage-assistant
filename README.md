# triage-assistant

An AI support-triage assistant for [identity-lab](../identity-lab), a self-contained single sign-on lab that can be broken on purpose. It plays the IT support desk of a fictional company, Northgate Supplies, and works from the lab's runbooks.

> **Status: work in progress.** Done so far: configuration, the secrets guard, the lab scenario reader, the repository checks, runbook retrieval that works from plain language, and ticket mode with Claude, Ollama and demo backends. Watch mode and the evals arrive in later sessions. The Ollama backend has been run live on two Qwen 2.5 14B models (see `docs/live-runs-ollama.md`); the Claude backend is tested against a fake client only.

Built with AI-assisted tooling (Claude Code).

## What it will do

- **Ticket mode:** takes a customer-style ticket, with or without pasted log lines, and answers with the likely cause, the log lines that prove it, the matching runbook, fix steps, a draft customer reply and a confidence level. If the evidence is thin it says so and lists the questions it would ask. It may conclude "not a lab fault" (a wrong password, an old bookmark) instead of forcing a runbook match.
- **Watch mode:** follows the lab's container logs and, when errors start, collects the relevant lines and triages them.
- **Backends:** the Claude API, Ollama on another machine on your LAN, or demo mode, which replays saved answers so reviewers need neither.
- **Output:** Markdown in the terminal. There is no web page: the lab already uses every port from 8180 to 8189.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # then fill in what you use; .env is gitignored
python -m pytest
```

`LAB_DIR` in `.env` points at identity-lab (default `../identity-lab`). The lab is only ever read.

## Retrieval

Only the best two or three runbooks go to the model. `python -m triage retrieve "it goes round in circles"` shows what a ticket would retrieve and why; add `--logs file` to include log lines.

How it ranks, with no model and no network:

1. BM25 over the runbooks' sections. The "how customers describe it" lines count most, so the runbooks' own customer language is searched.
2. `data/phrases.yaml` maps everyday wording ("goes round in circles", "says something about a redirect") to runbooks. It also has hints for user-side causes (an old bookmark, a forgotten password), which stop keyword overlap alone from forcing a runbook.
3. The lab's own error text and log patterns (`SCENARIO_EXPECT`, `SCENARIO_LOG`) and `event=` names in log lines count as strong evidence.

A match is **strong** if it has any evidence beyond keywords, and **weak** otherwise. The prompt will say which, and no match at all is a proper result.

### How well it works

`tests/retrieval_queries.yaml` has 53 plain-language queries (including unrelated and user-side tickets that should match nothing). Counting a query as right if the expected runbook is in the top three, or nothing is returned when nothing fits:

| Set | Queries | Plain keyword ranking | Phrases and evidence, first run | Now |
|---|---|---|---|---|
| dev (tuned on) | 20 | 17 | 20 | 20 |
| held-out | 19 | 12 | 17 | 19 |
| fresh | 14 | not run | 10 | 13 |

Be careful reading the last column. After the first run I fixed what the failures showed (a keyword floor, a word-coverage check, a better stemmer and a few phrases), so the held-out and fresh sets are no longer independent evidence; the "first run" column is the fair one. The one known miss is an unrelated ticket ("Why is my laptop so slow since the update?") that gets weak keyword matches. It is marked weak, so the model is told they are only possibilities. Your ticket set is the real test; until then, `tests/test_retrieval.py` has floors so retrieval can't quietly get worse.

### Known limitations

- **Vague whole-organisation outages are genuinely ambiguous.** "Everybody got kicked out at about nine this morning and nobody touched anything" fits runbooks 01 (expired signing certificate), 02 (clock skew), 04 (directory bind) and 17 (maintenance left on) equally well, because none of them has an error text to tell them apart. Retrieval gets this wrong in the one cold-set miss: it returns only runbook 08 (rate limit), as a **weak** match, because of words like "every" and "morning". That is why a weak-only result never gets a stated cause: the assistant asks questions instead (see Ticket mode below). The miss is recorded, not fixed.
- **Unrelated tickets can still get weak matches.** A laptop, printer or finance-group request returns weak keyword matches rather than nothing. They are labelled weak, and the answer then asserts nothing.

### The cold set

`tests/cold_queries.yaml` holds 24 queries written without seeing the retriever, the phrases file or the tuned sets. It was run once with no tuning and scored **23 of 24** (19 fault queries, 18 correct; 5 that should match nothing, all correct as "nothing strong"). It is pinned by hash in `tests/test_cold_set.py`, which also holds a regression floor of 23.

**It stops being independent evidence the moment anything is tuned against it.** If that ever happens, say so here and stop quoting the score as held-out.

## Ticket mode

```bash
python -m triage ticket tickets/001.md                       # a ticket file (see triage/tickets.py)
python -m triage ticket --text "The portal goes round in circles"
python -m triage ticket 001.md --logs lab.log --backend ollama
python -m triage ticket 001.md --backend claude --record     # also save the answer for demo mode
```

A ticket file is the customer's message, with an optional `## Log excerpt` section. Only its subject and body reach a model, never the other header fields.

**Two modes, chosen by how well the runbooks match:**

- **Diagnose** (at least one *strong* match): the answer may state the likely cause, with the log lines that prove it quoted word for word, the matching runbook, fix steps and a draft reply.
- **Clarify** (only weak matches, or none): the answer **asserts no cause**. It asks 3 to 6 questions drawn from the weak runbooks' symptoms and "Questions I'd ask the customer", and mentions those runbooks only as conditions ("if the symptoms match ..., runbook 15 may apply"). The code checks this: an answer with a cause, a fix, a confidence above low or "the cause is ..." wording is sent back once with the reasons, then replaced by an answer built by code. With the demo backend, clarify answers are built by code and no model is used.

Whatever a model returns is checked before you see it: quoted evidence that is not in the supplied text is dropped, the runbook is derived from the outcome rather than taken from the model, and confidence is capped when the evidence is thin.

**Demo mode** (`BACKEND=demo`, the default) needs no key and no GPU. Clarify answers are built by code. Diagnose answers are replayed from `demo/answers/`, saved by `--record` after a real run, and keyed by the ticket, the logs and the retrieved runbooks, so changing the prompt does not orphan them. Two are saved in `demo/answers/`, with their fixtures in `demo/fixtures/`.

**Claude** uses structured output (not a forced tool choice, which Claude Sonnet 5.5 rejects). If the API declines a request it can re-run it on a fallback model (`CLAUDE_REFUSAL_FALLBACK=1`, the default).

## Safety

- A value from the lab's own `.env` is a hard refusal anywhere: nothing is sent to a model.
- Tokens, cookies and SAML responses pasted into a **ticket** are masked, flagged to the engineer, and triage carries on. The customer reply then advises against sending such values.
- Secrets in **log lines** are masked and flagged the same way by default, since the lab masks its own logs and a secret there means something slipped through. Set `LOG_SECRETS=refuse` to stop instead.
- Whatever the settings, the whole prompt is checked again just before it is sent.
- Ticket and log text is treated as data, never as instructions.

## Local models

For a model of about 14B parameters on a 16 GB GPU, `OLLAMA_NUM_CTX` is sent with every request, and the prompt is budgeted to fit it (`OLLAMA_NUM_CTX` minus `OLLAMA_RESERVE_OUTPUT_TOKENS`). Nothing is cut silently:

- The ticket, the log snapshot and each runbook extract are trimmed to the budget, error-like log lines first, and every cut is reported above the answer.
- Sizes are estimated on the cautious side (3 characters a token for prose, 2.5 for logs and runbooks). The reply is also checked: if Ollama reports it read almost the whole window, that is an error, because it probably cut the prompt.
- An answer cut off by the length cap is an error too.
- The first request has its own, longer timeout (`OLLAMA_FIRST_TIMEOUT_S`) because it may load the model into the GPU.

The worst case is tested: three of the longest runbooks, a 60 KB log snapshot and a very long ticket, at windows of 8192, 12288 and 16384 tokens. It fits, but the window decides how much of each runbook the model sees (the runbooks run up to about 10,000 characters):

| `OLLAMA_NUM_CTX` | Each runbook extract, worst case | Prompt estimate |
|---|---|---|
| 8192 | about 2,000 characters | about 6,300 tokens |
| 12288 | about 3,400 characters | about 10,200 tokens |
| 16384 | about 5,000 to 5,700 characters | about 13,900 tokens |

Whether a given 14B model fits 12288 or 16384 on a 16 GB card depends on its quantisation, so check VRAM before raising it. To run Ollama on a different machine, such as a Windows PC with a GPU, see [docs/ollama-on-another-machine.md](docs/ollama-on-another-machine.md). What a real run measured, including a correction to the token estimate, is in [docs/live-runs-ollama.md](docs/live-runs-ollama.md).

## Latest scores

<!-- scores:start -->
No scores recorded yet.
<!-- scores:end -->

## Repository checks

`python -m pytest` includes a scan of every committed file for home paths, em dashes and addresses outside the lab's ranges. Add personal terms (your username, employer) one per line to `.private-terms`, which is gitignored, and the scan checks for those too.

## Names

The assistant's company, Northgate Supplies, and its system names live in `data/names.yaml`. Nothing depends on identity-lab using them.
