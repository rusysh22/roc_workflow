-- Demo data for showcasing the app.
-- Names and role assignments are taken from the two real workbook sheets shared
-- for IMN (PT Interport Multi Niaga / Batam) and MBN (PT Mitra Baruna Nusantara),
-- plus the centralized Buyer/Cost Reviewer team seeded under IMU (the HO entity).
-- Emails are SYNTHETIC placeholders (firstname.lastname@imugroup.com) -- replace
-- with real corporate emails before using this for anything beyond a demo.
-- Other entities (ILSS, KGTE, IRB, PGE, ISB, INDIS) are intentionally left empty
-- since no real assignment data was available for them -- this also demonstrates
-- the dashboard's coverage-gap indicator.

-- ---------- IMU (centralized Buyer / Cost Reviewer team + Group President Director) ----------

INSERT INTO user_assignments (full_name, email, entity_id, site_id, role_id, is_active) VALUES
  ('Yohanes K. Gatot Mulyono T', 'yohanes.mulyono@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMU'),
   (SELECT id FROM sites WHERE name = 'HO Jakarta' AND entity_id = (SELECT id FROM entities WHERE code = 'IMU')),
   (SELECT id FROM roles WHERE name = 'Buyer'), TRUE),
  ('Basuki Wibawa', 'basuki.wibawa@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMU'),
   (SELECT id FROM sites WHERE name = 'HO Jakarta' AND entity_id = (SELECT id FROM entities WHERE code = 'IMU')),
   (SELECT id FROM roles WHERE name = 'Buyer'), TRUE),
  ('Eko Santoso', 'eko.santoso@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMU'),
   (SELECT id FROM sites WHERE name = 'HO Jakarta' AND entity_id = (SELECT id FROM entities WHERE code = 'IMU')),
   (SELECT id FROM roles WHERE name = 'Buyer'), TRUE),
  ('Pawestri Wulan Ramadani', 'pawestri.ramadani@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMU'),
   (SELECT id FROM sites WHERE name = 'HO Jakarta' AND entity_id = (SELECT id FROM entities WHERE code = 'IMU')),
   (SELECT id FROM roles WHERE name = 'Buyer'), TRUE),
  ('Itjie Tandi Karang', 'itjie.karang@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMU'),
   (SELECT id FROM sites WHERE name = 'HO Jakarta' AND entity_id = (SELECT id FROM entities WHERE code = 'IMU')),
   (SELECT id FROM roles WHERE name = 'Buyer'), TRUE),
  ('Tirsa Awuy', 'tirsa.awuy@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMU'),
   (SELECT id FROM sites WHERE name = 'HO Jakarta' AND entity_id = (SELECT id FROM entities WHERE code = 'IMU')),
   (SELECT id FROM roles WHERE name = 'Buyer'), TRUE),

  ('Adhitya Syafta', 'adhitya.syafta@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMU'),
   (SELECT id FROM sites WHERE name = 'HO Jakarta' AND entity_id = (SELECT id FROM entities WHERE code = 'IMU')),
   (SELECT id FROM roles WHERE name = 'Cost Reviewer'), TRUE),
  ('Rini Yurnalis', 'rini.yurnalis@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMU'),
   (SELECT id FROM sites WHERE name = 'HO Jakarta' AND entity_id = (SELECT id FROM entities WHERE code = 'IMU')),
   (SELECT id FROM roles WHERE name = 'Cost Reviewer'), TRUE),
  ('Engelika Panggabean', 'engelika.panggabean@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMU'),
   (SELECT id FROM sites WHERE name = 'HO Jakarta' AND entity_id = (SELECT id FROM entities WHERE code = 'IMU')),
   (SELECT id FROM roles WHERE name = 'Cost Reviewer'), TRUE),
  ('Rasyid Arya Putra', 'rasyid.putra@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMU'),
   (SELECT id FROM sites WHERE name = 'HO Jakarta' AND entity_id = (SELECT id FROM entities WHERE code = 'IMU')),
   (SELECT id FROM roles WHERE name = 'Cost Reviewer'), TRUE),

  ('Adi Darma Shima', 'adi.shima@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMU'),
   (SELECT id FROM sites WHERE name = 'HO Jakarta' AND entity_id = (SELECT id FROM entities WHERE code = 'IMU')),
   (SELECT id FROM roles WHERE name = 'President Director'), TRUE);

-- ---------- MBN (PT Mitra Baruna Nusantara), site: Head Office ----------

INSERT INTO user_assignments (full_name, email, entity_id, site_id, role_id, is_active) VALUES
  ('Adi Darma Shima', 'adi.shima@imugroup.com',
   (SELECT id FROM entities WHERE code = 'MBN'),
   (SELECT id FROM sites WHERE name = 'Head Office' AND entity_id = (SELECT id FROM entities WHERE code = 'MBN')),
   (SELECT id FROM roles WHERE name = 'President Director'), TRUE),
  ('Surya Aribowo', 'surya.aribowo@imugroup.com',
   (SELECT id FROM entities WHERE code = 'MBN'),
   (SELECT id FROM sites WHERE name = 'Head Office' AND entity_id = (SELECT id FROM entities WHERE code = 'MBN')),
   (SELECT id FROM roles WHERE name = 'Director'), TRUE),
  ('Wien Goerindro', 'wien.goerindro@imugroup.com',
   (SELECT id FROM entities WHERE code = 'MBN'),
   (SELECT id FROM sites WHERE name = 'Head Office' AND entity_id = (SELECT id FROM entities WHERE code = 'MBN')),
   (SELECT id FROM roles WHERE name = 'Assignment Director'), TRUE),
  ('Ferdinand Tarigan', 'ferdinand.tarigan@imugroup.com',
   (SELECT id FROM entities WHERE code = 'MBN'),
   (SELECT id FROM sites WHERE name = 'Head Office' AND entity_id = (SELECT id FROM entities WHERE code = 'MBN')),
   (SELECT id FROM roles WHERE name = 'Assignment Director'), TRUE),
  ('Annas Aditya Farizqi', 'annas.farizqi@imugroup.com',
   (SELECT id FROM entities WHERE code = 'MBN'),
   (SELECT id FROM sites WHERE name = 'Head Office' AND entity_id = (SELECT id FROM entities WHERE code = 'MBN')),
   (SELECT id FROM roles WHERE name = 'PR Creator'), TRUE),
  ('Annas Aditya Farizqi', 'annas.farizqi@imugroup.com',
   (SELECT id FROM entities WHERE code = 'MBN'),
   (SELECT id FROM sites WHERE name = 'Head Office' AND entity_id = (SELECT id FROM entities WHERE code = 'MBN')),
   (SELECT id FROM roles WHERE name = 'SES Creator'), TRUE);

-- ---------- IMN (PT Interport Multi Niaga), site: Batam ----------

INSERT INTO user_assignments (full_name, email, entity_id, site_id, role_id, is_active) VALUES
  ('Yukki Nugrahawan Hanafi', 'yukki.hanafi@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMN'),
   (SELECT id FROM sites WHERE name = 'Batam' AND entity_id = (SELECT id FROM entities WHERE code = 'IMN')),
   (SELECT id FROM roles WHERE name = 'Director'), TRUE),
  ('Restrimaya Susiwi', 'restrimaya.susiwi@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMN'),
   (SELECT id FROM sites WHERE name = 'Batam' AND entity_id = (SELECT id FROM entities WHERE code = 'IMN')),
   (SELECT id FROM roles WHERE name = 'Deputy Director'), TRUE),
  ('Aji Darmadi', 'aji.darmadi@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMN'),
   (SELECT id FROM sites WHERE name = 'Batam' AND entity_id = (SELECT id FROM entities WHERE code = 'IMN')),
   (SELECT id FROM roles WHERE name = 'Assignment Director'), TRUE),
  ('Iman Gandi Mihardja', 'iman.mihardja@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMN'),
   (SELECT id FROM sites WHERE name = 'Batam' AND entity_id = (SELECT id FROM entities WHERE code = 'IMN')),
   (SELECT id FROM roles WHERE name = 'Assignment Director'), TRUE),
  ('Adi Umardani', 'adi.umardani@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMN'),
   (SELECT id FROM sites WHERE name = 'Batam' AND entity_id = (SELECT id FROM entities WHERE code = 'IMN')),
   (SELECT id FROM roles WHERE name = 'Dept Head'), TRUE),
  ('Azizi Syaldira Vitricia', 'azizi.vitricia@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMN'),
   (SELECT id FROM sites WHERE name = 'Batam' AND entity_id = (SELECT id FROM entities WHERE code = 'IMN')),
   (SELECT id FROM roles WHERE name = 'PR Creator'), TRUE),
  ('Azizi Syaldira Vitricia', 'azizi.vitricia@imugroup.com',
   (SELECT id FROM entities WHERE code = 'IMN'),
   (SELECT id FROM sites WHERE name = 'Batam' AND entity_id = (SELECT id FROM entities WHERE code = 'IMN')),
   (SELECT id FROM roles WHERE name = 'SES Creator'), TRUE);
