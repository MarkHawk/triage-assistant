# Live runs on Ollama (2026-10-26)

Two Qwen 2.5 14B models (Q4_K_M, 9.0 GB on disk) on Ollama 0.40.11, on an RTX 5060 with 16 GB, reached over the LAN. The case was a real `redirect-loop` from identity-lab: break it, load the page until the browser gives up, snapshot the logs, fix it, then triage the snapshot with the customer's ticket. The second case was the unrelated "printer offline" ticket (clarify mode). The ticket, the scoped logs and the printer ticket are in `demo/fixtures/`.

## What was measured

| Window | Memory used by the model | Where it ran |
|---|---|---|
| 8192 | 10.4 GB | 100% GPU |
| 12288 | 11.2 GB | 100% GPU |
| 16384 | 11.9 GB | 100% GPU |

Nothing was truncated, and no `PromptTruncated` error occurred at any size. Diagnose runs took about 17 to 22 seconds, and clarify runs about 11 to 19 seconds. The first request of each model and window adds about 5 seconds to load the model (about 56 seconds the very first time, from disk).

## The token estimate was wrong, and was fixed

The first runs showed the model reading more tokens than the assistant had estimated at the larger windows (8,718 against 7,706 at 16384). Probing each kind of text on its own showed why:

| Text | Characters per token |
|---|---|
| Prose (the ticket, the system prompt) | 4.3 to 5.2 |
| Runbook extracts | 3.44 at worst of all 19 |
| **Log lines** | **1.56** |

Log lines are expensive because timestamps and ids tokenise nearly a digit at a time. The assistant had assumed 2.5 for logs and runbooks alike. It now assumes 1.4 for logs and 3.0 for prose and runbooks, each below the measurement, and a test fails if they rise above it. The estimate now sits 34 to 38% above what the model actually reads. Docker's own nanosecond timestamp is also dropped from log lines that already carry a time.

If you use a different model family, check these ratios: the tokenizer decides them. The run still stops with an error rather than answering from a cut prompt, because Ollama reports how much it read.

## Findings from reading the answers

- **Diagnosis:** both models named `redirect-loop` with high confidence at every window, quoted the `force_https` line word for word, and found the `X-Forwarded-Proto` header as the cause.
- **Window size changed little here.** Going from 8192 to 16384 kept more log lines (63 omitted down to 42) but gave the same runbook text, because the per-runbook cap of 6000 characters binds before the window does. The answers were no better.
- **Full against scoped snapshot (8192):** the same diagnosis. The full snapshot had 95 lines against 77, left out 81 lines against 63, and the model read the same number of tokens. The lab was idle, so there was little noise to separate them. A longer, busier window would show more difference.
- **The two models:**
  - `qwen2.5-coder:14b` followed the clarify rules on every first attempt, but in one of three diagnose runs it pasted the runbook's code blocks as 12 numbered "steps".
  - `qwen2.5:14b` wrote cleaner steps and customer replies in diagnose mode, but broke the clarify rules on the first attempt in all three runs. A retry fixed it twice. Once the retry failed too, and the answer was built by code.
  - Single runs vary from one to the next even at temperature 0, so none of this is a ranking.

## Defects the live runs exposed, and fixes

| Defect | Fix |
|---|---|
| Log lines underestimated (above) | Calibrated ratios, with a test against the measurements |
| Customer reply written as the engineer to nobody, signed `[Your Name]` | The prompt says write to the customer as "we" and sign off as the company's IT Support; any placeholder left is removed and reported |
| Fix steps were the runbook's lines and code fences, numbered | The prompt asks for short plain steps and names the runbook section instead |
| Possibilities written as findings ("The issue is ..."); retry message did not say which | The violation now quotes the offending text and shows the allowed form; the prompt gives an example |
| Junk possibilities for an unrelated ticket | The prompt says to return none for something that is not a sign-in or network problem, and to ask what they are trying to do |
| Possibilities appeared in diagnose mode | Dropped in code, with a note |
| A correctly quoted log line was dropped as "not in the supplied text" | Quotes are checked against the logs as the model saw them, as well as the originals |
| A recording made at one window would not replay under another budget | The recording key now uses the ticket, the full logs and the retrieved runbooks only |

## Recordings

`demo/answers/` holds one answer of each kind, both made at a 12288 window: the diagnosis by `qwen2.5:14b` and the clarify answer by `qwen2.5-coder:14b`, each the model that did better on that case. Their metadata names the model and date, and nothing else. They replay with no network: `BACKEND=demo python -m triage ticket demo/fixtures/redirect-loop/ticket.md --logs demo/fixtures/redirect-loop/logs.txt`.

# Round 2 (2026-10-26): seven cases, both models, three runs each, window 12288

Changes made first: the ceiling on one runbook extract now grows with the window; the clarify prompt carries a worked example of a compliant answer; the default window is 12288.

Five scenarios were broken in the lab in turn, each with its own ticket and a log snapshot **scoped to the services involved** (`demo/fixtures/<scenario>/logs.txt`): the SAML app for `expired-signing-cert`; Keycloak, the directory and the OIDC app for `ldap-bind-failure`; Squid and the egress app for `proxy-auth-required`; the API and the OIDC app for `cors-blocked`; the proxy for `client-cert-missing`. With `redirect-loop` and the printer clarify case that makes seven cases and 42 runs. The tickets are plain customer wording; three contain an error message and two do not.

"Runbook right" means the outcome is the expected scenario and the model, not the fallback code, produced it. For the clarify case it means `needs_more_info`. "Rules held first time" means no retry was needed. "Evidence verified" counts the quoted log lines that were found word for word in the logs, out of those the model claimed.

| Case | Model | Runbook right | Rules held first time | Retry or fallback | Evidence verified | Time (average) |
|---|---|---|---|---|---|---|
| expired-signing-cert | qwen2.5-coder:14b | 3/3 | 3/3 | 0 retry, 0 fallback | 3/3 | 10.1 s |
| expired-signing-cert | qwen2.5:14b | 3/3 | 3/3 | 0 retry, 0 fallback | 3/3 | 17.7 s |
| ldap-bind-failure | qwen2.5-coder:14b | 0/3 | 3/3 | 0 retry, 0 fallback | 6/6 | 12.1 s |
| ldap-bind-failure | qwen2.5:14b | 3/3 | 3/3 | 0 retry, 0 fallback | 3/6 | 15.4 s |
| proxy-auth-required | qwen2.5-coder:14b | 0/3 | 3/3 | 0 retry, 0 fallback | 6/9 | 16.4 s |
| proxy-auth-required | qwen2.5:14b | 3/3 | 3/3 | 0 retry, 0 fallback | 3/6 | 16.4 s |
| cors-blocked | qwen2.5-coder:14b | 3/3 | 3/3 | 0 retry, 0 fallback | 0/3 | 10.5 s |
| cors-blocked | qwen2.5:14b | 3/3 | 3/3 | 0 retry, 0 fallback | 6/6 | 14.8 s |
| client-cert-missing | qwen2.5-coder:14b | 0/3 | 3/3 | 0 retry, 0 fallback | 9/9 | 13.0 s |
| client-cert-missing | qwen2.5:14b | 3/3 | 3/3 | 0 retry, 0 fallback | 3/3 | 9.1 s |
| redirect-loop | qwen2.5-coder:14b | 3/3 | 3/3 | 0 retry, 0 fallback | 3/3 | 14.1 s |
| redirect-loop | qwen2.5:14b | 3/3 | 3/3 | 0 retry, 0 fallback | 6/6 | 14.8 s |
| printer-offline (clarify) | qwen2.5-coder:14b | 3/3 | 3/3 | 0 retry, 0 fallback | n/a | 7.5 s |
| printer-offline (clarify) | qwen2.5:14b | 3/3 | 3/3 | 0 retry, 0 fallback | n/a | 6.0 s |

| Totals | Runbook right | Rules held first time | Retries | Fallbacks | Evidence verified | Time (average) |
|---|---|---|---|---|---|---|
| qwen2.5-coder:14b | 12/21 | 21/21 | 0 | 0 | 27/33 (82%) | 12.0 s |
| qwen2.5:14b | 21/21 | 21/21 | 0 | 0 | 24/30 (80%) | 13.5 s |

## What the counts say

- **`qwen2.5:14b` got the scenario right in all 21 runs. `qwen2.5-coder:14b` got 12 of 21.** The coder model answered `not_a_lab_fault` in all nine runs of `ldap-bind-failure`, `proxy-auth-required` and `client-cert-missing`, each time with **high confidence**. Its cause, evidence and customer reply were correct and described the fault; it only chose the wrong value for `outcome`. A reader of the whole answer would see the fault, but anything using the outcome field would miss it.
- **The worked example fixed the clarify rules.** Before it, the instruct model broke the clarify rules on its first attempt in all three printer runs. Now both models held them first time, in all six. In diagnose mode no run needed a retry, because there only an unusable reply forces one.
- **About 80% of quoted evidence lines verified for both models.** The dropped lines were real fabrications that the check is there to catch: a made-up nanosecond timestamp, a long line "tidied" so its quoting no longer matched, and the coder model quoting the browser's console message from the runbook text as though it were in the logs.
- **Speed is about the same:** 12.0 s against 13.5 s on average.
- **Runs 2 and 3 of each case were identical to each other** (same text, same time). At temperature 0 the models are close to deterministic, so three runs confirm stability rather than sample variation. Treat this as seven cases per model, not 21.
- **Limits:** the tickets and the scenario choice are mine, three tickets contain the exact error text (which makes retrieval easy), and the logs are short.

## Recommendation

Make **`qwen2.5:14b`** the default model. It was right every time, held the clarify rules, and costs about a second more per answer. Keep the coder model only if you want to compare. A check is worth adding either way (not yet built): if the outcome is `not_a_lab_fault` but a runbook matched strongly and the model supplied a cause and verified evidence, the answer contradicts itself, so retry once with that pointed out and otherwise cap the confidence.

## Fixtures and recordings

Fixtures and recordings were kept only for the scenarios where both models were right at least two times in three: `expired-signing-cert`, `cors-blocked`, `redirect-loop` and the printer clarify case. `ldap-bind-failure`, `proxy-auth-required` and `client-cert-missing` do not qualify, because the coder model failed them, so their fixtures are not in the repository. Each recording is the best of the six runs for its case: right, no retry, the highest share of evidence verified, no corrections needed, then fastest.
