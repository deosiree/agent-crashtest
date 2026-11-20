-- The sink is the "outside world": every row in refunds is a side effect that
-- really happened. The checker counts these rows; nothing else is trusted.

CREATE TABLE IF NOT EXISTS refunds (
    id              BIGSERIAL PRIMARY KEY,
    order_id        TEXT        NOT NULL,
    amount_cents    INTEGER     NOT NULL CHECK (amount_cents > 0),
    idempotency_key TEXT,
    request_hash    TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Deduplication is enforced by the database, not by application code that
-- checks first and inserts second: two concurrent requests with the same key
-- cannot both insert. Rows without a key are never deduplicated.
CREATE UNIQUE INDEX IF NOT EXISTS refunds_idempotency_key_uq
    ON refunds (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS refunds_order_id_idx ON refunds (order_id);

-- Every request the sink received, including replays and conflicts. This is
-- what separates "the client sent it twice" from "the effect happened twice".
CREATE TABLE IF NOT EXISTS request_log (
    id              BIGSERIAL PRIMARY KEY,
    order_id        TEXT        NOT NULL,
    idempotency_key TEXT,
    outcome         TEXT        NOT NULL CHECK (outcome IN ('created', 'replayed', 'conflict')),
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS request_log_order_id_idx ON request_log (order_id);

-- Whether a fault was applied to this request. Added after the first release,
-- so it is an ALTER: databases created earlier gain the column on startup.
ALTER TABLE request_log ADD COLUMN IF NOT EXISTS fault_applied BOOLEAN NOT NULL DEFAULT false;

-- The refund this request created or was answered with, so a duplicate can be
-- traced to the request that made it. Also added later, hence the ALTER.
ALTER TABLE request_log ADD COLUMN IF NOT EXISTS refund_id BIGINT;

-- Armed faults. "remaining" counts how many more effect-creating requests for
-- this order will have their response delayed after the effect is committed.
-- Replays and conflicts create no effect and do not consume a fault.
CREATE TABLE IF NOT EXISTS faults (
    order_id   TEXT PRIMARY KEY,
    mode       TEXT             NOT NULL CHECK (mode IN ('commit_then_delay')),
    delay_s    DOUBLE PRECISION NOT NULL CHECK (delay_s >= 0),
    remaining  INTEGER          NOT NULL CHECK (remaining >= 0)
);
