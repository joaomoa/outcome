# RFQ Matching & Settlement Engine

Core of a permissionless RFQ system for binary-outcome contracts.

## Quick start

```bash
createdb rfq_test   # once
pip install --break-system-packages -e ".[dev]"
export DATABASE_URL=postgresql+psycopg:///rfq_test
pytest -v
```

With Docker: `docker compose up -d` and use `DATABASE_URL=postgresql+psycopg://rfq:rfq@localhost:5432/rfq`.

## Layout

```
rfq_engine/
  engine.py    # RfqEngine — all business logic
  ledger.py    # balance mutations (available / reserved / locked)
  models.py    # Postgres tables
  enums.py     # statuses
  money.py     # premium & collateral formulas
```

One class to trace in the interview: `RfqEngine`. Pass `at=` — the current timestamp for that operation (tests use a fixed time).

## Capital formulas

Requester buys YES at price `p`, notional `N`:

- MM reserves `N * (1 - p)` on quote
- Requester locks `N * p` on accept
- YES → requester wins full pot; NO → MM keeps collateral

## Flow

`submit_request` → `submit_quote` → `run_matching` → `accept` → `initiate_resolution` → `resolve_leg` → `settle_request`

Resolve each leg: `engine.resolve_leg(leg_id, ResolutionOutcome.YES)`

## Docs

- `docs/state_machine.md`
- `docs/failure_modes.md`
- `docs/resolution_design.md`
- `docs/timing_design_note.md`
