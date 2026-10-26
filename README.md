# triage-assistant

An AI support-triage assistant for [identity-lab](../identity-lab), a self-contained single sign-on lab that can be broken on purpose. It plays the IT support desk of a fictional company, Northgate Supplies, and works from the lab's runbooks.

> **Status: work in progress.** Session 1 of the build is done: configuration, the secrets guard, the lab scenario reader and the repository checks. Triage itself arrives in later sessions.

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

## Safety

- A secret in lab logs, or any value from the lab's own `.env`, is a hard refusal: nothing is sent to a model.
- Tokens, cookies and SAML responses pasted into a **ticket** are masked, flagged to the engineer, and triage carries on. The customer reply then advises against sending such values.
- Ticket and log text is treated as data, never as instructions.

## Local models

For a model of about 14B parameters on a 16 GB GPU, `OLLAMA_NUM_CTX` is sent with every request and the prompt is budgeted to fit (`OLLAMA_NUM_CTX` minus `OLLAMA_RESERVE_OUTPUT_TOKENS`), so Ollama never truncates silently. Timeouts are separate for the first request, which includes loading the model.

## Latest scores

<!-- scores:start -->
No scores recorded yet.
<!-- scores:end -->

## Repository checks

`python -m pytest` includes a scan of every committed file for home paths, em dashes and addresses outside the lab's ranges. Add personal terms (your username, employer) one per line to `.private-terms`, which is gitignored, and the scan checks for those too.

## Names

The assistant's company, Northgate Supplies, and its system names live in `data/names.yaml`. Nothing depends on identity-lab using them.
