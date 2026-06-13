# State Machine

## Request lifecycle

```mermaid
stateDiagram-v2
    direction TB
    [*] --> Open: requester_submit
    Open --> Quoting: first_quote_arrives
    Quoting --> Presented: matching_all_legs_fill
    Quoting --> Failed: matching_any_leg_unfilled
    Quoting --> Expired: response_deadline_passed
    Presented --> Accepted: requester_accept
    Presented --> Rejected: requester_reject
    Presented --> Expired: accept_window_passed
    Presented --> Failed: quote_expired_on_accept
    Accepted --> EscrowLocked: funds_locked_txn
    EscrowLocked --> Resolved: initiate_resolution
    Resolved --> Settled: all_legs_paid_out
    Failed --> [*]
    Rejected --> [*]
    Expired --> [*]
    Settled --> [*]
```

## Transitions

| From | To | Trigger | Actor |
|------|-----|---------|-------|
| — | Open | `submit_request` | Requester |
| Open | Quoting | first quote submitted | MM |
| Quoting | Presented | `run_matching`, one MM quotes all legs; lowest `∏ pᵢ` wins | System |
| Quoting | Failed | `run_matching`, no MM covers all legs | System |
| Presented | Accepted | `accept` | Requester |
| Presented | Rejected | `reject` | Requester |
| Presented | Expired | `process_expirations` | System |
| Presented | Failed | `accept` with expired quote | System |
| EscrowLocked | Resolved | `initiate_resolution` | System |
| Resolved | Settled | `settle_request` after parlay resolved | System |

## Per-leg quote substates

| State | Meaning |
|-------|---------|
| `active` | MM quote live; no funds moved until accept |
| `selected` | Best quote chosen for leg; awaiting accept |
| `rejected` | Competing quote or request rejected; reservation released |
| `expired` | Accept window passed; reservation released |

## Multi-leg partial failure

**Scenario:** 3-leg request; legs 1 and 3 quoted by MM_A; leg 2 has no quote from any MM who also covers legs 1 and 3.

```mermaid
sequenceDiagram
    participant R as Request
    participant M as MatchingService
    participant DB as PostgresTxn

    M->>DB: BEGIN
    M->>R: SELECT FOR UPDATE
    M->>M: evaluate leg1 best quote OK
    M->>M: evaluate leg2 no quote
    M->>R: status = failed
    Note over DB: no quote.status = selected committed
    M->>DB: COMMIT
```

Leg 1 never durably reaches `selected` because matching evaluates all legs in one transaction before writing any `selected` status. If we naïvely committed leg 1 first, leg 1's MM would be bound while the request is unfilled — capital leak risk. Our design selects all or none.

On `failed`, active quotes remain (no capital was held).
