# RFQ Matching & Settlement Engine

Core of a permissionless RFQ system for binary-outcome contracts.

## Quick start

```bash
createdb rfq_test   # once
pip install --break-system-packages -e ".[dev]"
export DATABASE_URL=postgresql:///rfq_test
pytest -v
```

With Docker: `docker compose up -d` and use `DATABASE_URL=postgresql://rfq:rfq@localhost:5432/rfq`.

## Layout

```
schema.sql       # all tables — read this first
rfq_engine/
  engine.py      # business logic
  queries.py     # all SELECT/INSERT/UPDATE
  ledger.py      # balance UPDATEs
  enums.py
  errors.py
```

No ORM. SQL lives in `queries.py` and `ledger.py`; `engine.py` is rules and flow.

## Capital (buy YES, notional N, price p)

- MM reserves `N * (1 - p)` on quote (`ledger.reserve`)
- Requester locks `N * p` on accept (`ledger.lock_escrow`)

## Deadlines

- **Response deadline** — requester sets at submit (`response_deadline_seconds`)
- **Accept window** — venue policy (`ACCEPT_WINDOW_SECONDS`)

## Flow

`submit_request` → `submit_quote` → `run_matching` → `accept` → `initiate_resolution` → `resolve_leg` → `settle_request`

Resolve each leg: `engine.resolve_leg(leg_id, ResolutionOutcome.YES)`

## Docs

- `docs/state_machine.md`
- `docs/failure_modes.md`
- `docs/resolution_design.md`
- `docs/timing_design_note.md`
