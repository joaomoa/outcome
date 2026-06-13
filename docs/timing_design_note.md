# Timing Design Note

## Invariant to quote lifetime (seconds vs days)

- State machine transitions and ledger semantics (`available` / `locked`)
- `expires_at`, `response_deadline`, `accept_deadline` as absolute timestamps
- Per-operation `at` (optional kwarg; test default on engine, else wall clock)
- Best-quote ranking (parlay price → parlay size → mm_id)

## Not invariant

- How often a worker polls for expirations (`ExpiryService`, response deadline enforcement)
- Quote refresh / replace cadence for MMs
- Accept window duration (currently 300s constant; could be per-request config)
- Dispute window duration (`DISPUTE_WINDOW_SECONDS`, currently 2 hours; stored as `dispute_deadline` on propose)

## Built for cheap pivot

- All TTLs stored as `timedelta` inputs → absolute `datetime` on write
- fixed test default + `accept(..., at=...)` proves behavior is independent of wall clock
- Scheduler is a thin wrapper: `process_expirations()` + `process_resolution_expirations()` + future `fail_past_response_deadline()` — swap cron frequency without touching core services

## What we built today

Quote `expires_in_seconds` and request `response_deadline_seconds` are parameters at submission. Moving from second-level to day-level quotes requires only changing those values and running the expiry worker less frequently.
