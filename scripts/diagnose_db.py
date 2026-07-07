"""Read-only snapshot of the current production schema/data shape.

Run this against the real DATABASE_URL and paste the output back — it makes
zero changes, just reports what's actually there so the master-data import
script can be written to match reality instead of guesses.

Usage:
    DATABASE_URL=postgresql://... python3 scripts/diagnose_db.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import get_db_connection  # noqa: E402


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def main():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            print("=== TABLES ===")
            for r in q(cur, """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' ORDER BY table_name
            """):
                print(" ", r["table_name"])

            print("\n=== work_units (count + sample) ===")
            print("count:", q(cur, "SELECT COUNT(*) AS n FROM work_units")[0]["n"])
            for r in q(cur, """
                SELECT wu.id, wu.code, wu.project_name, wu.site_id,
                       e.code AS entity_code, s.name AS site_name
                FROM work_units wu
                JOIN entities e ON e.id = wu.entity_id
                LEFT JOIN sites s ON s.id = wu.site_id
                ORDER BY e.code, wu.sort_order, wu.code
            """):
                print(f"   {r['code']:22} entity={r['entity_code']:6} site_id={str(r['site_id']):5} site_name={r['site_name']}  project_name={r['project_name']}")

            print("\n=== sites (count + sample) ===")
            print("count:", q(cur, "SELECT COUNT(*) AS n FROM sites")[0]["n"])
            for r in q(cur, """
                SELECT s.id, e.code AS entity_code, s.name
                FROM sites s JOIN entities e ON e.id = s.entity_id
                ORDER BY e.code, s.name
            """):
                print(f"   id={r['id']:4} {r['entity_code']:6} {r['name']}")

            print("\n=== roles ===")
            for r in q(cur, "SELECT id, name, is_centralized FROM roles ORDER BY name"):
                print(f"   id={r['id']:4} {r['name']:25} centralized={r['is_centralized']}")

            print("\n=== user_assignments (count + role breakdown) ===")
            print("count:", q(cur, "SELECT COUNT(*) AS n FROM user_assignments")[0]["n"])
            for r in q(cur, """
                SELECT r.name AS role_name, COUNT(*) AS n
                FROM user_assignments ua JOIN roles r ON r.id = ua.role_id
                GROUP BY r.name ORDER BY r.name
            """):
                print(f"   {r['role_name']:25} {r['n']}")

            print("\n=== workpaper_rows (count + role breakdown) ===")
            print("count:", q(cur, "SELECT COUNT(*) AS n FROM workpaper_rows")[0]["n"])
            for r in q(cur, """
                SELECT wr.row_kind, r.name AS role_name, COUNT(*) AS n
                FROM workpaper_rows wr LEFT JOIN roles r ON r.id = wr.role_id
                GROUP BY wr.row_kind, r.name ORDER BY wr.row_kind, r.name
            """):
                print(f"   kind={r['row_kind']:10} role={str(r['role_name']):25} {r['n']}")

            print("\n=== workpaper_authority (count) ===")
            print("count:", q(cur, "SELECT COUNT(*) AS n FROM workpaper_authority")[0]["n"])

            print("\n=== amount_tiers ===")
            for r in q(cur, "SELECT id, seq, min_usd, max_usd FROM amount_tiers ORDER BY seq"):
                print(f"   id={r['id']} seq={r['seq']} {r['min_usd']} - {r['max_usd']}")

            print("\n=== tier_entities / tier_sites (counts) ===")
            for t in ("tier_entities", "tier_sites"):
                try:
                    print(f"   {t}:", q(cur, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"])
                except Exception as e:
                    print(f"   {t}: ERROR {e}")

            print("\n=== ses_entries columns present ===")
            for r in q(cur, """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'ses_entries' ORDER BY ordinal_position
            """):
                print("  ", r["column_name"])

            print("\n=== workpaper_rows columns present ===")
            for r in q(cur, """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'workpaper_rows' ORDER BY ordinal_position
            """):
                print("  ", r["column_name"])

            print("\n=== user_assignments columns present ===")
            for r in q(cur, """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'user_assignments' ORDER BY ordinal_position
            """):
                print("  ", r["column_name"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
