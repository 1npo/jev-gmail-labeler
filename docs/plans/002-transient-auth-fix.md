# Plan: don't treat transient network errors at startup as fatal auth failures

## Background

On 2026-10-19 the `jev-gmail-labeler` systemd user service died after a reboot and stayed dead for ~22 hours.

- The service started before DNS was up.
- `load_credentials()` (`src/jev_gmail_labeler/google_api/auth.py`) tried to refresh the token, hit `google.auth.exceptions.TransportError` (`Temporary failure in name resolution`), and raised `AuthError(..., transient=True)`.
- `cli.py` `main()` catches every `AuthError` and returns `EXIT_AUTH` (3), logging "Authorization failed ... Run `jev-gmail-labeler auth`". It ignores `e.transient`.
- The systemd unit has `RestartPreventExitStatus=2 3`, so exit 3 means no restart.

It was NOT the 7-day "Testing" refresh-token expiry (that would be a `RefreshError`/`invalid_grant`, and the service had run >7 days on the same token).

## Already done on the deploy machine (no repo change needed)

The unit file got an `ExecStartPre` that waits for `oauth2.googleapis.com` to resolve, plus `TimeoutStartSec=infinity`. User units cannot order after `network-online.target`, which is why it polls DNS.

## Changes to make in the repo

1. **`cli.py`, `main()`**: in the `except AuthError as e:` branch, if `e.transient` is true, log a transient-failure message (no "Run `jev-gmail-labeler auth`" hint, since re-auth won't help) and return a new retryable exit code. Keep returning `EXIT_AUTH` (3) for non-transient errors.
   - Add a constant such as `EXIT_TRANSIENT = 4` next to `EXIT_AUTH = 3` (around line 39).
   - Before choosing the number, check which codes are used (1, 2 and 3 are known: 2 is in `RestartPreventExitStatus`; look at `EXIT_UNEXPECTED`, `EXIT_INTERRUPTED`, `EXIT_CONFIG`). Pick one that isn't in `RestartPreventExitStatus`.
   - Keep the existing `str(e).startswith('TypeSafe')` branch behaviour.
2. **Check other `AuthError`/transient paths for the same bug**: `listen` startup, `google_api/pubsub.py` (~line 70) and `google_api/gmail.py` (~line 95). `service.py` lines ~121 and ~166 already honour `transient` inside the loop, so confirm the runtime loop keeps retrying on transient errors rather than exiting.
3. **Tests**: add a test that `main()` returns the transient exit code when `load_credentials` raises `AuthError('...', transient=True)`, and still returns 3 for non-transient. Mock the `TransportError` path in `load_credentials` too.
4. **Docs/unit file**: wherever the repo ships or documents the systemd unit (README, `contrib/`, etc.), update it:
   - Document the new exit code and keep it out of `RestartPreventExitStatus`.
   - Include the DNS-wait `ExecStartPre` and `TimeoutStartSec=infinity`, with a comment that user units can't depend on `network-online.target`:
     ```
     ExecStartPre=/bin/sh -c 'until getent hosts oauth2.googleapis.com >/dev/null; do sleep 3; done'
     TimeoutStartSec=infinity
     ```
   - Consider `Restart=on-failure` with `RestartSec=10` (current) so a transient exit retries every 10s.
5. **Document the Testing-state gotcha**: add a note to the README that the GCP OAuth consent screen must be set to "In production" (not "Testing"), otherwise refresh tokens expire after 7 days and the service needs an interactive `auth` re-login. Verify what the README says now.

## Verification

- Run the test suite and linters.
- Manually: with networking blocked (e.g. `unshare -n` or temporarily break DNS), run `jev-gmail-labeler listen` and confirm it exits with the new code and that systemd would restart it.
- Confirm that a bad or revoked token still exits 3 and is not restarted.

## Out of scope

- Releasing/tagging, or upgrading the installed copy on the deploy machine (`uv tool upgrade jev-gmail-labeler`). Do those after the change is merged.
