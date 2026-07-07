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
aplikasi (tabel `work_units`, `threshold_bands`, `workpaper_rows`,
`workpaper_authority`, `ses_entries`).

## Langkah

1. **(Sekali) buat skema** — jalankan seluruh `sql/migration.sql` di database.

2. **Generate seed** dari workbook terbaru:

   ```bash
   cd scripts
   python3 generate_seed.py "/path/Workflow_Approval_....xlsx" > ../sql/seed_workpaper.sql
   ```

   Setiap sheet unit (mis. `IMU-FIN`, `IMN-SBY`) diparse otomatis; sheet
   `Sheet1`, `IMU` (template transpose), dan yang bertanda `(Not Use)`
   dilewati. Kolom dicari berdasarkan label ("Level / Tingkat", "Name / Nama",
   dst.), jadi tahan terhadap pergeseran posisi kolom antar sheet.

3. **Muat data**:

   ```bash
   psql "$DATABASE_URL" -f sql/seed.sql            # entities/sites/roles (sekali)
   psql "$DATABASE_URL" -f sql/seed_workpaper.sql  # kertas kerja (idempoten)
   ```

   `seed_workpaper.sql` melakukan `TRUNCATE ... RESTART IDENTITY` di awal,
   jadi aman dijalankan ulang setiap ada revisi workbook.

## Utilitas

- `parse_workpaper.py <xlsx>` — cetak hasil parse sebagai JSON (untuk cek data).
- `render_preview.py <parsed.json> <CODE> <out_dir>` — render template Flask
  secara offline (tanpa DB) untuk verifikasi.
- `styled_preview.py <parsed.json> <CODE> <out.html>` — preview mandiri
  (inline CSS) yang mirip tampilan di aplikasi.

Ketika workbook direvisi (Rev12, dst.), cukup ulangi langkah 2–3.
