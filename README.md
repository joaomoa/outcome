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

## Capital (buy YES parlay, total notional ΣNᵢ, leg prices pᵢ)

- Parlay price: `∏ pᵢ` (stored on `requests.parlay_price` at match)
- Premium: `ΣNᵢ × ∏ pᵢ`; collateral: `ΣNᵢ × (1 − ∏ pᵢ)`
- On accept: both sides lock from `available` via `lock_parlay_escrow` (no quote-time holds)

## Deadlines

- **Response deadline** — requester sets at submit (`response_deadline_seconds`)
- **Accept window** — venue policy (`ACCEPT_WINDOW_SECONDS`)
- **Dispute window** — venue policy (`DISPUTE_WINDOW_SECONDS`); set on `propose_outcome`, enforced in `dispute_request`, auto-finalized by `process_resolution_expirations`

## Flow

`submit_request` → `submit_quote` → `run_matching` → `accept` → `initiate_resolution` → `report_leg_outcome` (all legs) → `propose_outcome` → `finalize_request` → `settle_request`

Multi-leg requests are parlays: YES only if every leg is YES. Report each leg's component outcome, then `propose_outcome(request_id)` computes the parlay. Disputes (`dispute_request`) apply to the whole request while `proposed`.

## Docs

- `docs/state_machine.md`
- `docs/failure_modes.md`
- `docs/resolution_design.md`
- `docs/timing_design_note.md`
