-- Entities
-- Names confirmed from source workbook where available (IMN, MBN);
-- others use the group code as a placeholder name pending confirmation.
INSERT INTO entities (code, name) VALUES
  ('IMU',   'IMU Group'),
  ('ILSS',  'ILSS'),
  ('IMN',   'PT Interport Multi Niaga'),
  ('KGTE',  'KGTE'),
  ('IRB',   'IRB'),
  ('PGE',   'PGE'),
  ('MBN',   'PT Mitra Baruna Nusantara'),
  ('ISB',   'ISB'),
  ('INDIS', 'INDIS');

-- Sites
-- Derived from the workbook's per-entity site tabs (e.g. "IMN-JKT", "IRB-SOR").
-- TNT and MJK codes are kept as-is (unconfirmed abbreviation) pending confirmation;
-- update the name once the real site name is known.
INSERT INTO sites (entity_id, name) VALUES
  ((SELECT id FROM entities WHERE code = 'IMU'),  'HO Jakarta'),

  ((SELECT id FROM entities WHERE code = 'ILSS'), 'Batam'),

  ((SELECT id FROM entities WHERE code = 'IMN'),  'Jakarta'),
  ((SELECT id FROM entities WHERE code = 'IMN'),  'Surabaya'),
  ((SELECT id FROM entities WHERE code = 'IMN'),  'Balikpapan'),
  ((SELECT id FROM entities WHERE code = 'IMN'),  'Makassar'),
  ((SELECT id FROM entities WHERE code = 'IMN'),  'TNT'),
  ((SELECT id FROM entities WHERE code = 'IMN'),  'Batam'),

  ((SELECT id FROM entities WHERE code = 'KGTE'), 'Jakarta'),
  ((SELECT id FROM entities WHERE code = 'KGTE'), 'Balikpapan'),

  ((SELECT id FROM entities WHERE code = 'IRB'),  'Jakarta'),
  ((SELECT id FROM entities WHERE code = 'IRB'),  'Balikpapan'),
  ((SELECT id FROM entities WHERE code = 'IRB'),  'Sorong'),

  ((SELECT id FROM entities WHERE code = 'PGE'),  'Jakarta'),
  ((SELECT id FROM entities WHERE code = 'PGE'),  'MJK'),

  ((SELECT id FROM entities WHERE code = 'MBN'),  'Head Office'),
  ((SELECT id FROM entities WHERE code = 'MBN'),  'Balikpapan'),
  ((SELECT id FROM entities WHERE code = 'MBN'),  'Morowali'),
  ((SELECT id FROM entities WHERE code = 'MBN'),  'Subang'),
  ((SELECT id FROM entities WHERE code = 'MBN'),  'Paser'),
  ((SELECT id FROM entities WHERE code = 'MBN'),  'Bekasi'),

  ((SELECT id FROM entities WHERE code = 'ISB'),  'Jakarta'),
  ((SELECT id FROM entities WHERE code = 'ISB'),  'Balikpapan');

-- Roles
INSERT INTO roles (name, is_centralized) VALUES
  ('PR Creator', FALSE),
  ('SES Creator', FALSE),
  ('Buyer', TRUE),
  ('Cost Reviewer', TRUE),
  ('Section Head', FALSE),
  ('Department Head', FALSE),
  ('Division Head', FALSE),
  ('Deputy Director', FALSE),
  ('Assignment Director', FALSE),
  ('Director', FALSE),
  ('President Director', FALSE);

-- Purchasing-authority amount tiers (USD-denominated master scale, shared by
-- every entity's Kertas Kerja; each entity's own exchange_rate_idr computes
-- the Rupiah figure shown alongside these bounds).
INSERT INTO amount_tiers (seq, min_usd, max_usd) VALUES
  (0, 0,       1000),
  (1, 1000,    5000),
  (2, 5000,    15000),
  (3, 15000,   80000),
  (4, 80000,   200000),
  (5, 200000,  2000000);
