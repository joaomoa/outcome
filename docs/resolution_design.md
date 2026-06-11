# Resolution Design

## Fund states through resolution

| Phase | Requester | MM |
|-------|-----------|-----|
| Escrow locked | `locked` = premium | `locked` = collateral |
| Pending resolution | unchanged | unchanged |
| Resolved YES (buyer) | `locked` → `available` (wins full pot) | forfeits `locked` |
| Resolved NO (buyer) | forfeits `locked` | `locked` → `available` (wins full pot) |

## Resolution

`resolve_leg(leg_id, outcome)` takes the outcome directly and pays out mechanically. No oracle lookup layer in the MVP.

## Multi-leg

Each leg resolves independently after `initiate_resolution`. `settle_request` requires all legs `resolved` before request → `settled`.
