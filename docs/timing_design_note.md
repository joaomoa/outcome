# Timing Design Note

## Invariant to quote lifetime (seconds vs days)

- State machine transitions and ledger semantics (`available` / `reserved` / `locked`)
- `expires_at`, `response_deadline`, `accept_deadline` as absolute timestamps
- `at: datetime` on `RfqEngine` — all time checks use that timestamp
- Best-quote ranking (price → size → created_at)

## Not invariant

- How often a worker polls for expirations (`ExpiryService`, response deadline enforcement)
- Quote refresh / replace cadence for MMs
- Accept window duration (currently 300s constant; could be per-request config)

## Built for cheap pivot

- All TTLs stored as `timedelta` inputs → absolute `datetime` on write
- fixed `at` in tests proves behavior is independent of wall clock
- Scheduler is a thin wrapper: `process_expirations()` + future `fail_past_response_deadline()` — swap cron frequency without touching core services

## What we built today

Quote `expires_in_seconds` and request `response_deadline_seconds` are parameters at submission. Moving from second-level to day-level quotes requires only changing those values and running the expiry worker less frequently.
