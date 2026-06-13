CREATE TYPE request_status AS ENUM (
    'open',
    'quoting',
    'presented',
    'escrow_locked',
    'resolved',
    'settled',
    'rejected',
    'expired',
    'failed'
);

CREATE TYPE quote_status AS ENUM (
    'active',
    'selected',
    'rejected',
    'expired'
);

CREATE TYPE resolution_status AS ENUM (
    'pending',
    'proposed',
    'disputed',
    'resolved'
);

CREATE TYPE resolution_outcome AS ENUM (
    'yes',
    'no',
    'void'
);

CREATE TABLE participants (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE balances (
    participant_id UUID PRIMARY KEY REFERENCES participants(id),
    available NUMERIC(20, 8) NOT NULL CHECK (available >= 0),
    locked NUMERIC(20, 8) NOT NULL CHECK (locked >= 0)
);

CREATE TABLE requests (
    id UUID PRIMARY KEY,
    requester_id UUID NOT NULL REFERENCES participants(id),
    stake NUMERIC(20, 8) NOT NULL,
    status request_status NOT NULL,
    response_deadline TIMESTAMPTZ NOT NULL,
    accept_deadline TIMESTAMPTZ,
    parlay_price NUMERIC(20, 8),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE legs (
    id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES requests(id),
    contract_description TEXT NOT NULL,
    leg_index INT NOT NULL,
    component_outcome resolution_outcome
);

CREATE TABLE quotes (
    id UUID PRIMARY KEY,
    leg_id UUID NOT NULL REFERENCES legs(id),
    mm_id UUID NOT NULL REFERENCES participants(id),
    price NUMERIC(10, 8) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    status quote_status NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_one_selected_per_leg ON quotes (leg_id) WHERE status = 'selected';

CREATE TABLE parlay_quotes (
    id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES requests(id),
    mm_id UUID NOT NULL REFERENCES participants(id),
    size NUMERIC(20, 8) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    status quote_status NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_one_selected_parlay_quote ON parlay_quotes (request_id) WHERE status = 'selected';

CREATE TABLE escrows (
    id UUID PRIMARY KEY,
    request_id UUID NOT NULL UNIQUE REFERENCES requests(id),
    requester_id UUID NOT NULL REFERENCES participants(id),
    mm_id UUID NOT NULL REFERENCES participants(id),
    requester_locked NUMERIC(20, 8) NOT NULL,
    mm_locked NUMERIC(20, 8) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE resolutions (
    id UUID PRIMARY KEY,
    request_id UUID NOT NULL UNIQUE REFERENCES requests(id),
    status resolution_status NOT NULL,
    outcome resolution_outcome,
    dispute_deadline TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
