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

# Second round of post-review changes (2026-10-27)

Three more changes from the review of the final run above. Like the first round they were made after reading the answers, so they are not independent of these 20 tickets.

| # | Change | How it is enforced |
|---|---|---|
| 9 | **Cause consistency.** The cause line must describe the runbook it diagnoses. t02's cause line was the worked example's sentence about certificates, under a redirect URI diagnosis | Compared with the runbook's own "Root cause, in one sentence" (and title). A mismatch is sent back once; if it persists, the runbook's own statement replaces it and confidence is capped at medium. High confidence now also needs the cause line to pass |
| 10 | **Ownership both ways (vendor persona).** What the ticket calls "your ..." (your API, your RADIUS server, your maintenance page) is ours, so the reply says what we will check or fix. What it calls "our ..." is the customer's, so "please check" or "we recommend". t03, t16 and t18 | Checked on the object of each "please check" or "we recommend": the first thing named after the verb, and anything joined to it by "and" or "or". A plain "the RADIUS server" in the reply takes the owner the ticket gave it; in the reply itself "our" and "your" flip. If the ticket points only at our side, asking the customer to fix anything is caught (t16). If the reply names something of ours but never says what we will do, that is caught too (t18) |
| 11 | **Quote marks and whitespace.** Straight and curly, single and double quotes, and runs of whitespace, count as equal when a quoted line is verified. t02 and t18 lost a correct quote because the model turned `"` into `'` | Code |

## How the cause check decides

Only distinctive words count: words that at most two runbooks' cause statements (with their titles) use, so generic words like "configuration" or "new" cannot tip it. A cause line agrees with the diagnosed runbook if it shares at least two of those words with that runbook's statement, and at least three quarters as many as with its best-matching runbook.

I chose those numbers by trying combinations against two kinds of evidence:

- **Independent of your tickets:** every runbook's own statement agrees with its runbook (19 of 19), and none of the 342 pairings of one runbook's statement with a different runbook is accepted. That is a test in `tests/test_answer.py`.
- **From your tickets' runs:** the 44 diagnosed cause lines from the three runs so far. The setting passes all 43 that describe the right runbook and fails the one that does not (t02, final run). An earlier, looser version wrongly failed run 1's correct t02 line, which is how the distinctive-word rule came about. So these thresholds were set with the real-ticket outputs in view.

## What the checks find in the final run's saved answers

Applied to the stored answers of the final run, without calling the model again:

| Ticket | Cause line | Ownership | Dropped quotes now verified |
|---|---|---|---|
| t02 | **does not match** runbook 03 | | 1 of 1 |
| t03 | ok | **asks the customer to check our RADIUS server**; never says what we will do | |
| t06 | ok | | 1 of 1 |
| t10 | ok | | 0 of 1 (a real mismatch) |
| t16 | ok | **asks the customer to turn off our maintenance mode** | |
| t17 | ok | | 0 of 1 (a real mismatch) |
| t18 | ok | **names our API but never says what we will check** | 1 of 1 |

No other diagnosed answer is flagged by the new checks.

## Limits

- **Ownership is read from wording.** It looks for things named after "your", "our", "my" or "the" from a fixed list of nouns (server, API, proxy, page, controller and so on). A generic word such as "page" or "side" in the reply only matches the ticket's full phrase, because "the page" could be anyone's. Something the ticket never names, such as the proxy in t16, is judged from the ticket as a whole: if it names only our things, asking the customer to fix anything is caught.
- **The code repairs are mechanical.** For example, "Please check the shared secret on both the new controller and the RADIUS server" becomes "We will check the RADIUS server on our side, and we recommend that you check the new controller on yours." It is correct, but it reads like a template. The flag above the answer says when code did it.
- **The real-ticket set was rerun once after the third round** (run C below), not after this one on its own.

# Third round, and all three runs side by side (2026-10-27)

Changes since the second round, again made after reading the answers:

| # | Change |
|---|---|
| 12 | The worked example in the diagnose prompt is now about a shared calendar, something the lab cannot produce. An answer that copies any part of it naming the calendar or mailbox is caught and repaired. The old example, about certificates, had turned up word for word as t02's cause line |
| 13 | A dropped quote that is the runbooks' own text (a sample log line, an error page, a symptom description) is labelled "quoted the runbook's own example, not the ticket or logs" in the engineer section, and counted separately in the eval report |
| 14 | The prompt says: runbook text (sample logs, error pages, symptom descriptions) is reference material, never evidence; evidence must come from the ticket or the supplied logs |

Run C is the one rerun after rounds two and three: same ticket file (hash `3b953480b286`), same model and window, ticket text only. The rule figures apply **today's** rules to each run's saved answers, so all three are judged the same way. Run A had never been told about them.

| Id | Expected | A: before review | B: after round 1 | C: after rounds 2 and 3 |
|---|---|---|---|---|
| t01 | 01, 02 | clarify | clarify | clarify |
| t02 | 03 | redirect-uri-mismatch, medium; wording | redirect-uri-mismatch, **high** by quoting itself; **cause line leaked from the example** | redirect-uri-mismatch, medium |
| t03 | 05 | radius-shared-secret-mismatch; **asks for the secret**, "we will" on their controller, wording | same scenario; **asks them to check our RADIUS server** | same scenario; "We will check the shared secret configuration on our side" |
| t04 | 04 | clarify | clarify | clarify |
| t05 | 02 | clock-skew; wording | clock-skew | clock-skew |
| t06 | 10, 06 | proxy-bad-gateway; lab term, wording | proxy-bad-gateway | proxy-bad-gateway |
| t07 | 06, 10 | proxy-bad-gateway; wording | proxy-bad-gateway | proxy-bad-gateway |
| t08 | 07 | proxy-gateway-timeout; wording | proxy-gateway-timeout | proxy-gateway-timeout |
| t09 | 08 | proxy-rate-limit; lab term, wording | proxy-rate-limit | proxy-rate-limit |
| t10 | 09 | proxy-forwarded-headers; lab term, wording | proxy-forwarded-headers | proxy-forwarded-headers |
| t11 | 11 | proxy-auth-required; wording | proxy-auth-required | proxy-auth-required |
| t12 | 13, 01 | **not_a_lab_fault (wrong)** | proxy-tls-interception | proxy-tls-interception |
| t13 | 14 | proxy-bypass; wording | proxy-bypass | proxy-bypass |
| t14 | 15 | redirect-loop; lab term, wording | redirect-loop | redirect-loop |
| t15 | 16, 19 | clarify | clarify | clarify |
| t16 | 17 | proxy-maintenance; lab term, wording | proxy-maintenance; **asks them to turn off our maintenance mode** | proxy-maintenance, **high** by the documented-text rule; "We will turn off maintenance mode on the proxy" |
| t17 | 18, 16 | client-cert-missing (clarify was expected) | same | same |
| t18 | 19 | cors-blocked; "we will", secrets | cors-blocked; **never says what we will check** | cors-blocked; code added "We will check the API on our side" |
| t19 | none | clarify | service request (no model) | service request (no model) |
| t20 | none | clarify | clarify | clarify |

| | A | B | C |
|---|---|---|---|
| Retrieval right | 20/20 | 20/20 | 20/20 |
| Mode appropriate | 19/20 | 19/20 | 19/20 |
| Cause right (of those diagnosed) | 14/15 | 15/15 | 15/15 |
| Answers breaking a rule in force today | 14/15 | 4/15 | **0/15** |
| Needed a second request | 0 | 5 | 7 |
| Corrected by code after two attempts | 0 | 3 | 2 |
| High confidence | 0 | 1 (t02, by quoting itself: a bug, since fixed) | 1 (t16, documented text) |
| Quotes from the runbooks presented as evidence | 1 (t10) | 2 (t10, t17) | 2 (t16, t17) |
| Other unverified quotes | 1 | 3 | 0 |
| Average time per ticket | 11.2 s | 12.7 s | 13.9 s |

Earlier in this document I counted run A as 15 of 15 answers breaking a rule. Judged by the full rules as they stand today it is 14: t12 answered `not_a_lab_fault`, which the wording rule does not apply to.

## Found in run C, not fixed

- **The identity provider is ours only if the ticket says so.** t02's reply says "Please check the registered redirect URI in the identity provider". As the vendor of the sign-in service, the identity provider is ours, but the ticket never says "your IdP", so the ownership check cannot tell. A fix would treat the identity provider and the sign-in service as ours by default in vendor mode.
- **t18 asks for a screenshot of the browser's Network tab.** That is not asking for a secret in words, but a Network tab screenshot can show cookies and tokens. The secrets rule does not catch it.

# Fourth round, and run D (2026-10-27)

Changes since run C, made after reviewing it:

| # | Change |
|---|---|
| 15 | The confirming question must ask about something the ticket does not already state. A prompt rule with an example (t02 asked whether the URL had changed; t09 asked whether it was a 429 or a 503) |
| 16 | One persona and one sign-off for every reply, whichever mode or source made it. The model no longer signs off; code adds "Sign-in service support" in vendor mode, or "<company> IT Support" in internal mode (`SUPPORT_SIGNOFF` overrides). The system prompt's opening line follows the persona too: in vendor mode it no longer says it is Northgate's IT desk |
| 17 | Never ask for a screenshot of the Network tab, a HAR file or request headers, which can carry cookies and tokens; ask for the console error text and the status code instead. Part of the secrets rule. Runbook sentences that ask for these (runbook 19 asks for a Network tab screenshot) are left out of the extracts the model sees; the runbooks themselves are untouched |
| 18 | `VENDOR_OWNS` (default: sign-in service, identity provider, API) lists what is ours in vendor mode when the ticket does not say "your". What the ticket calls "our ..." still wins |
| 19 | The ticket file gained a header saying it is fictional, so its hash changed from `3b953480b286` to `d5c9c4c6f211`. No ticket changed |

Every column is judged by **today's** rules, which are stricter than when B and C were reported (they now include the Network tab rule and `VENDOR_OWNS`), and clarify answers are included. That is why B and C show more breaks here than in the tables above.

| Id | Expected | A | B | C | D |
|---|---|---|---|---|---|
| t01 | 01, 02 | clarify | clarify | clarify | clarify |
| t02 | 03 | redirect-uri-mismatch; wording | redirect-uri-mismatch, high; cause | redirect-uri-mismatch; who does what | redirect-uri-mismatch |
| t03 | 05 | radius-shared-secret-mismatch; secrets, wording | radius-shared-secret-mismatch; who does what | radius-shared-secret-mismatch | radius-shared-secret-mismatch (code corrected) |
| t04 | 04 | clarify | clarify | clarify | clarify |
| t05 | 02 | clock-skew; wording | clock-skew | clock-skew | clock-skew |
| t06 | 10, 06 | proxy-bad-gateway; lab term, wording | proxy-bad-gateway | proxy-bad-gateway | proxy-bad-gateway |
| t07 | 06, 10 | proxy-bad-gateway; wording | proxy-bad-gateway | proxy-bad-gateway | proxy-bad-gateway |
| t08 | 07 | proxy-gateway-timeout; wording | proxy-gateway-timeout | proxy-gateway-timeout | proxy-gateway-timeout |
| t09 | 08 | proxy-rate-limit; lab term, wording | proxy-rate-limit | proxy-rate-limit | proxy-rate-limit |
| t10 | 09 | proxy-forwarded-headers; lab term, who does what, wording | proxy-forwarded-headers | proxy-forwarded-headers | proxy-forwarded-headers (code corrected) |
| t11 | 11 | proxy-auth-required; wording | proxy-auth-required; who does what | proxy-auth-required; who does what | proxy-auth-required (code corrected) |
| t12 | 13, 01 | not_a_lab_fault (**wrong**) | proxy-tls-interception | proxy-tls-interception | proxy-tls-interception (code corrected) |
| t13 | 14 | proxy-bypass; who does what, wording | proxy-bypass; who does what | proxy-bypass; who does what | proxy-bypass (code corrected) |
| t14 | 15 | redirect-loop; lab term, wording | redirect-loop | redirect-loop | redirect-loop |
| t15 | 16, 19 | clarify | clarify | clarify | clarify |
| t16 | 17 | proxy-maintenance; lab term, wording | proxy-maintenance; who does what | proxy-maintenance, high | proxy-maintenance, high (code corrected) |
| t17 | 18, 16 | client-cert-missing; who does what, wording | client-cert-missing | client-cert-missing | client-cert-missing |
| t18 | 19 | cors-blocked; who does what, secrets, wording | cors-blocked; who does what, secrets | cors-blocked; secrets | cors-blocked (code corrected) |
| t19 | none | clarify | service request | service request | service request |
| t20 | none | clarify | clarify | clarify | clarify |

| | A | B | C | D |
|---|---|---|---|---|
| Retrieval right | 20/20 | 20/20 | 20/20 | 20/20 |
| Mode appropriate | 19/20 | 19/20 | 19/20 | 19/20 |
| Cause right (of those diagnosed) | 14/15 | 15/15 | 15/15 | 15/15 |
| Answers breaking a rule in force today (service requests excluded) | 14/20 | 6/19 | 4/19 | **0/19** |
| Replies signed "Northgate Supplies IT Support" | 5 | 4 | 4 | **0** |
| Needed a second request | 0 | 5 | 7 | **10** |
| Corrected by code after two attempts | 0 | 3 | 2 | **7** |
| High confidence | 0 | 1 | 1 | 1 (t16, documented text) |
| Runbook text quoted as evidence | 1 | 2 | 2 | 4 |
| Other unverified quotes | 2 | 3 | 0 | 0 |
| Average time per ticket | 11.2 s | 12.7 s | 13.9 s | **16.8 s** |

## What run D shows

- **What you asked about holds.**
  - t02 now says "We will check this and update the configuration" about the identity provider, and asks "Could you confirm the exact URL users are trying to access", not whether the URL changed.
  - t09 asks whether the first login of the day works, not whether it was a 429 or a 503.
  - Every reply ends with "Sign-in service support", clarify answers included.
  - No reply asks for the Network tab, a HAR file or headers.
- **It costs more.** The model needed a second request for 10 of 19 answers and code corrected 7, against 7 and 2 in run C, and an answer takes about 17 seconds instead of 14. Most corrections are about who does what (code applied them to t10, t13 and t16) and the hedged wording (t03, t10, t12). Each is flagged above the answer.
- **Quoting runbook text as evidence went up**, from 2 to 4. Those quotes are labelled and never count as evidence, but the model does it more than before.

## Found in run D, not fixed

- **t09's reply says "Please check if a rate limit was recently added or changed on the proxy".** That reverse proxy is in front of our identity provider, so it is ours, but the ticket does not say so and "proxy" is not in `VENDOR_OWNS`. Adding it there would also claim proxies that belong to customers, such as the egress proxy in t11, so I have left it as it is.
