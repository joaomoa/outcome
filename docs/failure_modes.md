# Failure Modes

## Handled races and partial failures

| Failure | Handling |
|---------|----------|
| **Multi-leg partial fill** | `run_matching` requires one MM to quote all legs; lowest parlay price (`∏ pᵢ`) wins; otherwise `failed` |
| **Quote expiry before accept** | `accept` checks `expires_at`; marks quotes `rejected`, request `failed` |
| **Accept window expiry** | `process_expirations` marks quotes `expired`, request `expired` |
| **Double accept** | Status check on request; second accept sees `escrow_locked` → `ConflictError` |
| **Insufficient MM funds at accept** | `lock_parlay_escrow()` raises `InsufficientFundsError`; txn rolls back |
| **Insufficient requester funds at accept** | `lock_parlay_escrow()` raises; txn rolls back, no partial escrow |
| **Competing quotes on same leg** | On accept, non-selected active quotes → `rejected` (no ledger movement) |
| **DB rollback** | Each `RfqEngine` public method uses `conn.transaction()` — failure rolls back the whole operation |

## Idempotency

`accept` is safe to retry only before success: row lock + status check prevents duplicate escrow. A second accept after success fails with `ConflictError`.

## Stubbed / future hardening

- **Response deadline auto-fail:** production would run a worker to fail quoting requests past `response_deadline` and release quotes.
- **Quote withdrawal:** MM cannot cancel quote without an explicit `withdraw_quote` (not implemented).
- **Resolution deadline:** legs stuck in `pending` would eventually auto-propose `VOID` or escalate to `disputed` per venue policy (see `docs/resolution_design.md`).
- **Dispute window expiry:** `process_resolution_expirations` auto-finalizes unchallenged `proposed` parlays past `dispute_deadline`.

## Capital leak prevention

Locked funds only move via `lock_parlay_escrow` on accept. Quotes are non-binding until then — both sides must have sufficient `available` at accept time.
