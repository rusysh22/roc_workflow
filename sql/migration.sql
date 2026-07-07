-- Safe to re-run at any time: every statement is idempotent, so this file
-- can be used both to create the schema from scratch and to bring an older
-- deployment (e.g. one seeded before the users/kertas-kerja tables existed)
-- up to date without erroring on tables that already exist.

CREATE TABLE IF NOT EXISTS entities (
  id SERIAL PRIMARY KEY,
  code VARCHAR(20) UNIQUE NOT NULL,
  name TEXT NOT NULL,
  exchange_rate_idr NUMERIC(14,2) NOT NULL DEFAULT 16000
);
ALTER TABLE entities ADD COLUMN IF NOT EXISTS exchange_rate_idr NUMERIC(14,2) NOT NULL DEFAULT 16000;

CREATE TABLE IF NOT EXISTS sites (
  id SERIAL PRIMARY KEY,
  entity_id INTEGER REFERENCES entities(id),
  name TEXT NOT NULL
);
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sites_entity_id_name_key') THEN
    ALTER TABLE sites ADD CONSTRAINT sites_entity_id_name_key UNIQUE (entity_id, name);
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS roles (
  id SERIAL PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  is_centralized BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS user_assignments (
  id SERIAL PRIMARY KEY,
  full_name TEXT NOT NULL,
  email TEXT NOT NULL,
  entity_id INTEGER REFERENCES entities(id),
  site_id INTEGER REFERENCES sites(id),
  role_id INTEGER REFERENCES roles(id),
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(email, entity_id, role_id)
);

CREATE TABLE IF NOT EXISTS approval_levels (
  id SERIAL PRIMARY KEY,
  entity_id INTEGER REFERENCES entities(id),
  level_order INTEGER NOT NULL,
  role_id INTEGER REFERENCES roles(id),
  label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  full_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS changelog (
  id SERIAL PRIMARY KEY,
  admin_name TEXT NOT NULL,
  action TEXT NOT NULL,
  entity_code TEXT,
  field_changed TEXT,
  old_value TEXT,
  new_value TEXT,
  changed_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Kertas Kerja (Workpaper) model
-- Mirrors the Human Capital "Workflow Approval" workbook, where each
-- sheet is one purchasing-authority matrix for an entity + unit.
-- ============================================================

-- One row per workbook sheet, e.g. IMU-FIN, IMN-SBY.
CREATE TABLE IF NOT EXISTS work_units (
  id SERIAL PRIMARY KEY,
  entity_id INTEGER REFERENCES entities(id),
  code TEXT UNIQUE NOT NULL,
  project_name TEXT NOT NULL,
  sort_order INTEGER DEFAULT 0
);

-- Master scale of USD purchasing-authority tiers (columns of the matrix).
-- Shared by every entity; each entity's own exchange_rate_idr converts a
-- tier's USD bounds into the Rupiah figure shown alongside it.
CREATE TABLE IF NOT EXISTS amount_tiers (
  id SERIAL PRIMARY KEY,
  seq INTEGER UNIQUE NOT NULL,
  min_usd NUMERIC(18,2) NOT NULL,
  max_usd NUMERIC(18,2)  -- NULL = unbounded (this tier and above)
);

-- Master list of job titles, referenced by workpaper_rows so the same
-- title always reads identically across every unit.
CREATE TABLE IF NOT EXISTS positions (
  id SERIAL PRIMARY KEY,
  title TEXT UNIQUE NOT NULL
);

-- Authority-holder rows within a unit (President Director ... Cost Reviewer,
-- plus Buyer and PR Creator). row_kind: 'authority' | 'buyer' | 'creator'.
-- The level itself is a role from the shared `roles` master table (the same
-- one used by the Assignments module), not free text.
CREATE TABLE IF NOT EXISTS workpaper_rows (
  id SERIAL PRIMARY KEY,
  work_unit_id INTEGER REFERENCES work_units(id) ON DELETE CASCADE,
  row_kind TEXT NOT NULL DEFAULT 'authority',
  seq INTEGER NOT NULL,
  role_id INTEGER REFERENCES roles(id),
  person_name TEXT,
  position_id INTEGER REFERENCES positions(id),
  comment TEXT
);
ALTER TABLE workpaper_rows ADD COLUMN IF NOT EXISTS role_id INTEGER REFERENCES roles(id);
ALTER TABLE workpaper_rows ADD COLUMN IF NOT EXISTS position_id INTEGER REFERENCES positions(id);

-- Y/N per tier for an authority row (the heart of the matrix).
CREATE TABLE IF NOT EXISTS workpaper_authority (
  id SERIAL PRIMARY KEY,
  row_id INTEGER REFERENCES workpaper_rows(id) ON DELETE CASCADE,
  tier_id INTEGER REFERENCES amount_tiers(id),
  required BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE(row_id, tier_id)
);
ALTER TABLE workpaper_authority ADD COLUMN IF NOT EXISTS tier_id INTEGER REFERENCES amount_tiers(id);

-- Service Entry Sheet block: creator/approver per tier.
CREATE TABLE IF NOT EXISTS ses_entries (
  id SERIAL PRIMARY KEY,
  work_unit_id INTEGER REFERENCES work_units(id) ON DELETE CASCADE,
  tier_id INTEGER REFERENCES amount_tiers(id),
  creator_name TEXT,
  approver_name TEXT
);
ALTER TABLE ses_entries ADD COLUMN IF NOT EXISTS tier_id INTEGER REFERENCES amount_tiers(id);
