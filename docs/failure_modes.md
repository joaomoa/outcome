# Failure Modes

## Handled races and partial failures

| Failure | Handling |
|---------|----------|
| **Multi-leg partial fill** | `MatchingService.run_matching` evaluates all legs in one txn; any unfilled leg → `failed`, zero quotes set to `selected` |
| **Quote expiry before accept** | `AcceptanceService.accept` checks `expires_at`; releases all MM reservations, marks `failed` |
| **Accept window expiry** | `ExpiryService.process_expirations` releases all active/selected reservations, marks `expired` |
| **Double accept** | Status check on request; second accept sees `escrow_locked` → `ConflictError` |
| **Insufficient MM funds at quote** | `reserve()` raises `InsufficientFundsError`; txn rolls back, no quote inserted |
| **Insufficient requester funds at accept** | `lock_escrow()` raises; txn rolls back, no partial escrow |
| **Competing quotes on same leg** | On accept, non-selected active quotes → `rejected` + reservation released |
| **DB rollback** | Each `RfqEngine` public method uses `conn.transaction()` — failure rolls back the whole operation |

## Idempotency

`accept` is safe to retry only before success: row lock + status check prevents duplicate escrow. A second accept after success fails with `ConflictError`.

## Stubbed / future hardening

- **Response deadline auto-fail:** production would run a worker to fail quoting requests past `response_deadline` and release quotes.
- **Quote withdrawal:** MM cannot cancel quote without an explicit `withdraw_quote` (not implemented); reservations held until reject/expiry/accept.

## Capital leak prevention

Reservations are always tied to a `Quote` row with `reserved_amount`. Every release path (`reject`, `expire`, accept-competitor) calls `release_reservation` with the exact amount stored on the quote. Locked funds only move via `lock_escrow` after all legs pass validation.
