# Changes made after reviewing the first real-ticket run

Everything here was made **after** the author read the first run of the 20 real tickets (2026-10-26). They are post-review changes, not part of the design that run tested, so the two runs below are not independent: the second was produced by code written in response to the first. No retrieval phrases were added and the ticket file was not touched (its SHA-256 starts `3b953480b286` in both runs).

Both runs: `qwen2.5:14b`, window 12288, ticket text only, no logs, one run each. The retrieval column is the same in both because retrieval did not change.

## What changed

| # | Change | How it is enforced |
|---|---|---|
| 1 | A customer reply never asks for a password, shared secret, key, token or cookie ("Please confirm the current shared secret" was t03) | Checked in code; the model is sent the offending sentence and asked again; if it still breaks the rule, code removes the sentence |
| 2 | `SUPPORT_PERSONA=vendor\|internal`, default `vendor`. A vendor says "please check" or "we recommend", never "we will", about anything the ticket calls the customer's own (our server, our side, their proxy or network) | Prompt, then a check; code rewrites "we will update" to "we recommend that you update" |
| 3 | Below high confidence the reply says "the most likely cause is" and ends with a confirming question. Only high may say "the cause is" | Prompt (with a worked example), then a check; code rewords and adds the runbook's own question |
| 4 | Nothing lab-specific in fix steps, replies or customer questions: `scripts/`, `.state/`, `LAB_HOST`, `docker compose`, Keycloak, `idlab` | Check; code drops commands and renames the rest ("the identity provider", "the host name") |
| 5 | Fix steps are actions, not questions | Check; code moves a question step to the customer questions |
| 6 | The t12 case: if a runbook matched strongly, there is no user-side hint and the outcome is `not_a_lab_fault`, retry once pointing at the strong match, even with no evidence. If it survives, confidence is capped at low and the answer is flagged | The contradiction check no longer needs a stated cause or evidence |
| 7 | Account lockouts and admin requests (unlock, add user, reset, change access) are service requests: a short reply pointing to the account administrator, no diagnostic questions | Code answers them, with no model, when nothing matched strongly. A ticket that also describes a real fault matches a runbook strongly and goes the normal way |
| 8 | High confidence with no log lines is allowed only when all three hold: retrieval is strong, the ticket contains the runbook's documented error text word for word (a Symptom code block), and the diagnosed scenario belongs to that runbook. Otherwise it is capped at medium | Code |

**Two readings I made that you may want to change.** In (8) a documented line only counts if it appears nowhere in any other runbook's Symptom code blocks, even inside a longer line. Without that, a ticket saying just "502 Bad Gateway" (shown by both runbooks 06 and 10) would earn high confidence for 10, which is the 06 and 10 ambiguity again. And in (8), the customer's own words quoted as "evidence" no longer count as verified evidence: only quoted log lines do. The first run after these changes found tickets reaching high by quoting themselves; that was a bug in my confidence cap, and I fixed it and reran.

## Before and after

Automated columns only. The send-ready verdicts are not in this table: the first run's report on disk had an empty verdict column when I looked, so I could not read yours.

| Id | Expected | Before: mode / outcome / confidence / cause | After: mode / outcome / confidence / cause | Rule breaks before |
|---|---|---|---|---|
| t01 | 01, 02 | clarify / needs_more_info / low / n/a | clarify / needs_more_info / low / n/a | |
| t02 | 03 | diagnose / redirect-uri-mismatch / medium / right | diagnose / redirect-uri-mismatch / **high** / right | wording |
| t03 | 05 | diagnose / radius-shared-secret-mismatch / medium / right | same | **asks for the secret**, "we will", wording |
| t04 | 04 | clarify / needs_more_info / low / n/a | same | |
| t05 | 02 | diagnose / clock-skew / medium / right | same | wording |
| t06 | 10, 06 | diagnose / proxy-bad-gateway / medium / right | same | wording, lab term |
| t07 | 06, 10 | diagnose / proxy-bad-gateway / medium / right | same | wording |
| t08 | 07 | diagnose / proxy-gateway-timeout / medium / right | same | wording |
| t09 | 08 | diagnose / proxy-rate-limit / medium / right | same | wording, lab term |
| t10 | 09 | diagnose / proxy-forwarded-headers / medium / right | same | "we will", wording, lab term |
| t11 | 11 | diagnose / proxy-auth-required / medium / right | same | wording |
| t12 | 13, 01 | diagnose / **not_a_lab_fault** / medium / **wrong** | diagnose / **proxy-tls-interception** / medium / **right** | wording |
| t13 | 14 | diagnose / proxy-bypass / medium / right | same | wording |
| t14 | 15 | diagnose / redirect-loop / medium / right | same | wording, lab term |
| t15 | 16, 19 | clarify / needs_more_info / low / n/a | same | |
| t16 | 17 | diagnose / proxy-maintenance / medium / right | same | wording, lab term |
| t17 | 18, 16 | diagnose / client-cert-missing / medium / right (**mode wrong**: clarify expected) | same | wording |
| t18 | 19 | diagnose / cors-blocked / medium / right | same | wording |
| t19 | none | clarify / needs_more_info / low / n/a | **request** / not_a_lab_fault / medium / n/a (service request, no model) | |
| t20 | none | clarify / needs_more_info / low / n/a | same | |

| | Before | After |
|---|---|---|
| Retrieval right | 20 of 20 | 20 of 20 |
| Mode appropriate | 19 of 20 | 19 of 20 (a service request counts as clarify) |
| Cause right | 14 of 15 diagnosed | **15 of 15** |
| Diagnosed answers breaking at least one of the new rules (the rules applied to the saved replies) | **15 of 15** | **0 of 15** |
| High confidence | 0 | 1 (t02, by the documented-text rule) |
| Needed a second request | 0 (no rule existed) | 5 of 15 |
| Corrected by code after two failed attempts | 0 | 3 of 15 |
| Average time per ticket | 11.2 s | 12.7 s |

"Rule breaks before" are counted by applying the new rules to the first run's saved replies; the model had not been told about them. Wording (not saying "most likely", no confirming question) was by far the most common.

## Honest notes

- **The model does not follow the wording rule reliably.** A first version of the prompt got 13 of 14 answers corrected by code. Adding a worked example of a correctly hedged reply cut that to 3. When code corrects an answer it is flagged above the answer and listed in the engineer section, and the result is mechanical ("This is the most likely explanation so far, and we would like to confirm it. To confirm: ...?"). Read those.
- **There were two runs on the new code.** The first had the confidence bug described above (t02 and t12 came out high by quoting the ticket) and the wording prompt without the example. I discarded it and reran once on the final code; its report is still in `results/` for anyone who wants to compare. Only the final run is reported here.
- **t06 stays a recorded limitation.** The ticket's real clue is the group membership ("always the same people"); the assistant still answers `proxy-bad-gateway`, which the table counts as right because 06 and 10 are both accepted, but it missed the clue that points to 10. No phrase was added.
- **t17 stays a mode miss.** The ticket quotes a 400 and nothing more, retrieval matches 18 strongly, and the assistant diagnoses where you expected questions.
- **The persona rule is broad.** When the ticket mentions anything of the customer's, any "we will fix/change/check/..." in the reply is rewritten, including a harmless one about our own service. "We will update you" and "We will look into it" are left alone.
- **Service requests are recognised by wording**, so an unusual phrasing will go to the model instead, and a wording like "reset" next to a real fault that only matched weakly would be answered as a request.
- **Recordings were redone** for the demo, because the old ones predate these rules (see the README).
