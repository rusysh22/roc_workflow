# Setup Awal: User Login

Aplikasi mengharuskan login untuk semua halaman. Buat akun awal dengan:

```bash
cd scripts
python3 seed_users.py > ../sql/seed_users.sql
psql "$DATABASE_URL" -f ../sql/seed_users.sql
```

Daftar user dan password default diatur di `scripts/seed_users.py` (`USERS`,
`DEFAULT_PASSWORD`). Setiap user **wajib ganti password** saat login pertama
kali (`must_change_password`). Menjalankan ulang script ini aman — user yang
sudah ada (dan sudah ganti password) tidak akan ter-reset
(`ON CONFLICT (email) DO NOTHING`).

# Import Kertas Kerja dari Workbook Human Capital

Alur mengubah workbook Excel "Workflow Approval IMU Group" menjadi data
aplikasi. Setiap bagian yang tampak di kertas kerja punya master data sendiri,
bukan teks bebas:

| Tampil di kertas kerja | Master data | Tabel |
|---|---|---|
| Kolom nominal (US$1k, US$5k, ...) | Skala tier USD global + `exchange_rate_idr` per entity | `amount_tiers`, `entities.exchange_rate_idr` |
| Level (President Director, Director, ...) | Sama dengan tabel `roles` yang dipakai modul Assignments | `roles` |
| Position (jabatan) | Daftar jabatan ternormalisasi | `positions` |
| Nama orang, komentar | Teks per baris (memang bebas, ini bukan kategori) | `workpaper_rows` |

Label nominal (mis. "US$1k – < US$5k or Rp.16,000,000 – < Rp.80,000,000")
**tidak disimpan sebagai teks** — dihitung saat request dari `amount_tiers.min_usd/max_usd`
dikali `entities.exchange_rate_idr` (lihat `lib/money.py`). Ganti kurs satu
entity cukup update satu angka (`UPDATE entities SET exchange_rate_idr = ...`),
tidak perlu edit teks band satu-satu.

## Langkah

1. **(Sekali) buat skema** — jalankan seluruh `sql/migration.sql` di database,
   lalu `sql/seed.sql` (mengisi `roles`, `amount_tiers`, dll — termasuk role
   "Section Head" yang tidak ada di modul Assignments awal).

2. **Generate seed** dari workbook terbaru:

   ```bash
   cd scripts
   python3 generate_seed.py "/path/Workflow_Approval_....xlsx" > ../sql/seed_workpaper.sql
   ```

   Setiap sheet unit (mis. `IMU-FIN`, `IMN-SBY`) diparse otomatis; sheet
   `Sheet1`, `IMU` (template transpose), dan yang bertanda `(Not Use)`
   dilewati. Kolom dicari berdasarkan label ("Level / Tingkat", "Name / Nama",
   dst.), jadi tahan terhadap pergeseran posisi kolom antar sheet. Level dari
   workbook di-resolve ke `roles.id` (lihat `LEVEL_ALIASES` di
   `generate_seed.py` untuk varian ejaan seperti "Departement Head"), dan
   setiap Position baru otomatis ditambahkan ke tabel `positions`.

3. **Muat data**:

   ```bash
   psql "$DATABASE_URL" -f sql/seed.sql            # entities/roles/amount_tiers (sekali)
   psql "$DATABASE_URL" -f sql/seed_workpaper.sql  # kertas kerja (idempoten)
   ```

   `seed_workpaper.sql` melakukan `TRUNCATE ... RESTART IDENTITY` di awal
   untuk `work_units`/`workpaper_rows`/dst., jadi aman dijalankan ulang setiap
   ada revisi workbook. Tabel `positions` bersifat aditif (`ON CONFLICT DO
   NOTHING`) — tidak pernah direset supaya referensi lama tidak putus.

## Utilitas

- `parse_workpaper.py <xlsx>` — cetak hasil parse sebagai JSON (untuk cek data).
- `render_preview.py <parsed.json> <CODE> <out_dir>` — render template Flask
  secara offline (tanpa DB) untuk verifikasi.
- `styled_preview.py <parsed.json> <CODE> <out.html>` — preview mandiri
  (inline CSS) yang mirip tampilan di aplikasi.

Ketika workbook direvisi (Rev12, dst.), cukup ulangi langkah 2–3.
