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
