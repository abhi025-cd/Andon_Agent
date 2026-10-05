-- Mock MES schema for the Andon project
-- Safe to re-run: every statement is idempotent.

CREATE TABLE IF NOT EXISTS stations (
    station_id   TEXT PRIMARY KEY,
    line_id      TEXT NOT NULL,
    process_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicles (
    vin         TEXT PRIMARY KEY,
    created_ts  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS station_passes (
    id          BIGSERIAL PRIMARY KEY,
    vin         TEXT NOT NULL REFERENCES vehicles(vin),
    station_id  TEXT NOT NULL REFERENCES stations(station_id),
    ts          TIMESTAMPTZ NOT NULL,
    measured    JSONB NOT NULL DEFAULT '{}'::jsonb,
    result      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_passes_station_ts ON station_passes (station_id, ts);
CREATE INDEX IF NOT EXISTS idx_passes_vin        ON station_passes (vin);

CREATE TABLE IF NOT EXISTS alarms (
    id          BIGSERIAL PRIMARY KEY,
    station_id  TEXT NOT NULL REFERENCES stations(station_id),
    ts          TIMESTAMPTZ NOT NULL,
    code        TEXT NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'warning',
    dedup_key   TEXT NOT NULL UNIQUE   -- idempotent alarm handling
);

CREATE TABLE IF NOT EXISTS downtime_events (
    id          BIGSERIAL PRIMARY KEY,
    station_id  TEXT NOT NULL REFERENCES stations(station_id),
    start_ts    TIMESTAMPTZ NOT NULL,
    end_ts      TIMESTAMPTZ,
    reason_code TEXT
);

CREATE TABLE IF NOT EXISTS tool_changes (
    id          BIGSERIAL PRIMARY KEY,
    station_id  TEXT NOT NULL REFERENCES stations(station_id),
    ts          TIMESTAMPTZ NOT NULL,
    details     JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS incidents (
    id          BIGSERIAL PRIMARY KEY,
    alarm_id    BIGINT REFERENCES alarms(id),
    status      TEXT NOT NULL DEFAULT 'open',
    root_cause  TEXT,
    created_ts  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_actions (   -- audit log
    id          BIGSERIAL PRIMARY KEY,
    incident_id BIGINT REFERENCES incidents(id),
    agent       TEXT NOT NULL,
    tool        TEXT NOT NULL,
    args        JSONB,
    result      JSONB,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed stations
INSERT INTO stations (station_id, line_id, process_type) VALUES
    ('ST-010', 'LINE-1', 'body_welding'),
    ('ST-020', 'LINE-1', 'paint_booth'),
    ('ST-030', 'LINE-1', 'door_cockpit_fit'),
    ('ST-040', 'LINE-1', 'bolting'),
    ('ST-050', 'LINE-1', 'end_of_line_test')
ON CONFLICT DO NOTHING;

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS station_id  TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS signal      TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS detected_at TIMESTAMPTZ;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS evidence    JSONB;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS diagnosis   JSONB;
-- idempotency: at most one open incident per station + signal
CREATE UNIQUE INDEX IF NOT EXISTS uq_open_incident
    ON incidents (station_id, signal) WHERE status = 'open';

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS containment JSONB;

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS report TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS report_review JSONB;