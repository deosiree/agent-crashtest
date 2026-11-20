-- Result store: every results file, loaded as one run with its trials.
-- The JSON files in results/ stay the record; these tables are an index over
-- them, so that runs of different experiments can be queried side by side.
-- Loading is idempotent, and a file whose contents changed is refused.

CREATE SCHEMA IF NOT EXISTS results;

CREATE TABLE IF NOT EXISTS results.runs (
    id           BIGSERIAL PRIMARY KEY,
    file         TEXT        NOT NULL UNIQUE,  -- path relative to the repository
    sha256       TEXT        NOT NULL,
    experiment   TEXT        NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL,
    parameters   JSONB       NOT NULL DEFAULT '{}',
    environment  JSONB       NOT NULL DEFAULT '{}',
    provenance   JSONB,                        -- absent in files written before it existed
    summary      JSONB       NOT NULL DEFAULT '[]',
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS results.trials (
    run_id            BIGINT  NOT NULL REFERENCES results.runs (id) ON DELETE CASCADE,
    position          INTEGER NOT NULL,        -- index in the file's trials list
    cell              JSONB   NOT NULL,        -- the experiment's factor levels
    order_id          TEXT    NOT NULL,
    effects_committed INTEGER NOT NULL,
    requests_received INTEGER,
    verdict           TEXT,                    -- absent in files written before the checker
    fault_confirmed   BOOLEAN,
    raw               JSONB   NOT NULL,        -- the trial exactly as the file has it
    PRIMARY KEY (run_id, position)
);

CREATE INDEX IF NOT EXISTS trials_cell_idx ON results.trials USING gin (cell);
