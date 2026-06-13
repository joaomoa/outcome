CREATE TABLE participants (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE balances (
    participant_id UUID PRIMARY KEY REFERENCES participants(id),
    available NUMERIC(20, 8) NOT NULL CHECK (available >= 0),
    reserved NUMERIC(20, 8) NOT NULL CHECK (reserved >= 0),
    locked NUMERIC(20, 8) NOT NULL CHECK (locked >= 0)
);

CREATE TABLE requests (
    id UUID PRIMARY KEY,
    requester_id UUID NOT NULL REFERENCES participants(id),
    status TEXT NOT NULL,
    response_deadline TIMESTAMPTZ NOT NULL,
    accept_deadline TIMESTAMPTZ,
    parlay_price NUMERIC(20, 8),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE legs (
    id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES requests(id),
    contract_description TEXT NOT NULL,
    notional NUMERIC(20, 8) NOT NULL,
    leg_index INT NOT NULL,
    component_outcome TEXT
);

CREATE TABLE quotes (
    id UUID PRIMARY KEY,
    leg_id UUID NOT NULL REFERENCES legs(id),
    mm_id UUID NOT NULL REFERENCES participants(id),
    price NUMERIC(10, 8) NOT NULL,
    size NUMERIC(20, 8) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    reserved_amount NUMERIC(20, 8) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_one_selected_per_leg ON quotes (leg_id) WHERE status = 'selected';

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
    status TEXT NOT NULL,
    outcome TEXT,
    dispute_deadline TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
