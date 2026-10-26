# How the break-it loop will trigger failures

Result of the session 1 spike. Nothing in identity-lab was changed.

## Finding

The lab's own tests container can drive one login attempt for one app, which is
all the break-it loop needs.

```
docker compose --project-directory <lab> run --rm \
    -v <scratch>:/results -e E2E_USER=dave tests oidc --expect ok
```

- It is the same command `scripts/test.sh` uses (`browser_as`). Exit status 0
  means the expectation held.
- Targets are the values of `SCENARIO_APP` in the scenarios: saml, oidc,
  saml-proxy, oidc-proxy, oidc-egress, oidc-edge, oidc-api, oidc-api-late,
  oidc-cors and mtls. All but one scenario names one of these. The exception is
  `radius`, which `test.sh` drives with `compose exec nas nas-auth <user>`
  (password on stdin).
- `--expect-error TEXT` passes when the page shows TEXT, and each scenario's
  `SCENARIO_EXPECT` is exactly that text. So the trigger for a broken scenario is
  the same call with its `SCENARIO_EXPECT`; the trigger after the fix is
  `--expect ok`.
- Mounting a scratch folder over `/results` keeps the screenshots and page HTML
  out of the lab's `test-results/`, so the lab's working tree stays clean.
- Tried once on the healthy lab: `oidc --expect ok` as dave passed in about
  6 seconds, and `git status` in the lab stayed clean.

## Consequences for the design

- No Playwright code of our own is needed. The harness calls the lab's image.
- It needs the lab's `.env` (read by `docker compose` itself) and the
  `tests` image already built (`scripts/test.sh` builds it).
- Each trigger takes a few seconds, except `oidc-api-late`, which waits 15 s by
  design. That scenario's trigger is `oidc-api-late`; `api-bearer-expired` uses it.
- Tried against a broken scenario too, on `redirect-loop` (a proxy-only change):

  | Step | Result | Time |
  |---|---|---|
  | `scripts/break.sh redirect-loop` | broken | 2.2 s |
  | trigger `oidc-edge --expect-error ERR_TOO_MANY_REDIRECTS` | pass | 1.9 s |
  | `docker compose logs --timestamps --since 5m` snapshot | 1097 lines, 21 `force_https` | 0.4 s |
  | `scripts/fix.sh redirect-loop` | healthy | 2.2 s |
  | trigger `oidc-edge --expect ok` | pass | 2.4 s |
  | `scripts/status.sh` | all 21 scenarios healthy | |

  `git status` in the lab stayed clean. A whole cycle for a proxy scenario is
  about 10 seconds of lab time; the assistant's own wait comes on top.
