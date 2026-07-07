CREATE TABLE entities (
  id SERIAL PRIMARY KEY,
  code VARCHAR(20) UNIQUE NOT NULL,
  name TEXT NOT NULL
);

CREATE TABLE sites (
  id SERIAL PRIMARY KEY,
  entity_id INTEGER REFERENCES entities(id),
  name TEXT NOT NULL
);

CREATE TABLE roles (
  id SERIAL PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  is_centralized BOOLEAN DEFAULT FALSE
);

CREATE TABLE user_assignments (
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

CREATE TABLE approval_levels (
  id SERIAL PRIMARY KEY,
  entity_id INTEGER REFERENCES entities(id),
  level_order INTEGER NOT NULL,
  role_id INTEGER REFERENCES roles(id),
  label TEXT NOT NULL
);

CREATE TABLE changelog (
  id SERIAL PRIMARY KEY,
  admin_name TEXT NOT NULL,
  action TEXT NOT NULL,
  entity_code TEXT,
  field_changed TEXT,
  old_value TEXT,
  new_value TEXT,
  changed_at TIMESTAMP DEFAULT NOW()
);
