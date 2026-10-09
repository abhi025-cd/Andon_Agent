-- Mock MES schema, machine level.
-- WARNING: drops and recreates every table. Fine for simulated data.
DROP TABLE IF EXISTS approvals, agent_actions, incidents, tool_changes,
    downtime_events, alarms, station_passes, vehicles, machines, stations CASCADE;

CREATE TABLE stations (
    station_id   TEXT PRIMARY KEY,
    plant_id     TEXT NOT NULL,
    line_id      TEXT NOT NULL,
    process_type TEXT NOT NULL
);

CREATE TABLE machines (
    machine_id  TEXT PRIMARY KEY,
    station_id  TEXT NOT NULL REFERENCES stations(station_id),
    description TEXT
);

CREATE TABLE vehicles (
    vin        TEXT PRIMARY KEY,
    created_ts TIMESTAMPTZ NOT NULL
);

CREATE TABLE station_passes (
    id         BIGSERIAL PRIMARY KEY,
    vin        TEXT NOT NULL REFERENCES vehicles(vin),
    station_id TEXT NOT NULL REFERENCES stations(station_id),
    machine_id TEXT NOT NULL REFERENCES machines(machine_id),
    ts         TIMESTAMPTZ NOT NULL,
    measured   JSONB NOT NULL DEFAULT '{}'::jsonb,
    result     TEXT NOT NULL,
    UNIQUE (vin, machine_id)                  -- idempotent replays
);
CREATE INDEX idx_passes_machine_ts ON station_passes (machine_id, ts);
CREATE INDEX idx_passes_station_ts ON station_passes (station_id, ts);
CREATE INDEX idx_passes_vin        ON station_passes (vin);

CREATE TABLE alarms (
    id         BIGSERIAL PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(station_id),
    machine_id TEXT NOT NULL REFERENCES machines(machine_id),
    ts         TIMESTAMPTZ NOT NULL,
    code       TEXT NOT NULL,
    severity   TEXT NOT NULL DEFAULT 'warning',
    dedup_key  TEXT NOT NULL UNIQUE           -- idempotent alarm handling
);

CREATE TABLE downtime_events (
    id          BIGSERIAL PRIMARY KEY,
    station_id  TEXT NOT NULL REFERENCES stations(station_id),
    machine_id  TEXT REFERENCES machines(machine_id),
    start_ts    TIMESTAMPTZ NOT NULL,
    end_ts      TIMESTAMPTZ,
    reason_code TEXT
);

CREATE TABLE tool_changes (
    id         BIGSERIAL PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(station_id),
    machine_id TEXT NOT NULL REFERENCES machines(machine_id),
    ts         TIMESTAMPTZ NOT NULL,
    details    JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE incidents (
    id            BIGSERIAL PRIMARY KEY,
    station_id    TEXT NOT NULL REFERENCES stations(station_id),
    machine_id    TEXT NOT NULL REFERENCES machines(machine_id),
    signal        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',
    detected_at   TIMESTAMPTZ NOT NULL,
    root_cause    TEXT,
    evidence      JSONB,
    diagnosis     JSONB,
    containment   JSONB,
    report        TEXT,
    report_review JSONB,
    created_ts    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- idempotency: at most one open incident per machine + signal
CREATE UNIQUE INDEX uq_open_incident ON incidents (machine_id, signal) WHERE status = 'open';

CREATE TABLE agent_actions (                  -- audit log
    id          BIGSERIAL PRIMARY KEY,
    incident_id BIGINT REFERENCES incidents(id),
    agent       TEXT NOT NULL,
    tool        TEXT NOT NULL,
    args        JSONB,
    result      JSONB,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
    id          BIGSERIAL PRIMARY KEY,
    incident_id BIGINT REFERENCES incidents(id),
    action      TEXT NOT NULL,
    decision    TEXT NOT NULL,
    decided_by  TEXT,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed: simulated plant layout (not a real OEM layout)
INSERT INTO stations (station_id, plant_id, line_id, process_type) VALUES
    ('ST-010', 'PLANT-01', 'LINE-01', 'body_welding'),
    ('ST-020', 'PLANT-01', 'LINE-01', 'environmental_process'),
    ('ST-030', 'PLANT-01', 'LINE-01', 'fitment'),
    ('ST-040', 'PLANT-01', 'LINE-01', 'critical_fastening'),
    ('ST-050', 'PLANT-01', 'LINE-01', 'end_of_line_test');

INSERT INTO machines (machine_id, station_id, description) VALUES
    ('M-0101', 'ST-010', 'Weld unit A'),
    ('M-0102', 'ST-010', 'Weld unit B'),
    ('M-0201', 'ST-020', 'Humidity sensor'),
    ('M-0202', 'ST-020', 'Booth temperature'),
    ('M-0301', 'ST-030', 'Fitment unit A'),
    ('M-0302', 'ST-030', 'Fitment unit B'),
    ('M-0401', 'ST-040', 'Front-left fastening'),
    ('M-0402', 'ST-040', 'Front-right fastening'),
    ('M-0403', 'ST-040', 'Rear-left fastening'),
    ('M-0404', 'ST-040', 'Rear-right fastening'),
    ('M-0501', 'ST-050', 'Leak test A'),
    ('M-0502', 'ST-050', 'Leak test B');