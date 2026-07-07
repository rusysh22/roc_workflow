import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.db import get_db_connection

def seed_data():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            print("Looking for ILSS Entity...")
            cur.execute("SELECT id FROM entities WHERE code = 'ILSS'")
            ilss = cur.fetchone()
            if not ilss:
                cur.execute("INSERT INTO entities (code, name, exchange_rate_idr) VALUES ('ILSS', 'PT INTERPORT LOGISTIC & SUPPORT SERVICES', 16000) RETURNING id")
                ilss_id = cur.fetchone()["id"]
            else:
                ilss_id = ilss["id"]
            
            print("Creating Site Semarang...")
            cur.execute("SELECT id FROM sites WHERE name = 'SMG (Semarang)' AND entity_id = %s", (ilss_id,))
            site = cur.fetchone()
            if not site:
                cur.execute("INSERT INTO sites (entity_id, name) VALUES (%s, 'SMG (Semarang)') RETURNING id", (ilss_id,))
                smg_id = cur.fetchone()["id"]
            else:
                smg_id = site["id"]

            print("Fetching Roles...")
            cur.execute("SELECT id, name FROM roles")
            role_map = {r["name"]: r["id"] for r in cur.fetchall()}

            print("Assigning Employees to ILSS - Semarang...")
            # Use distinct emails to avoid unique constraint collisions
            employees = [
                ("Budi Santoso", "budi.smg@interport.com", "President Director"),
                ("Rina Amelia", "rina.smg@interport.com", "Director"),
                ("Joko Susilo", "joko.smg@interport.com", "Cost Reviewer"),
                ("Siti Maimunah", "siti.smg@interport.com", "Cost Reviewer"),
                ("Agus Pratama", "agus.smg@interport.com", "Buyer"),
                ("Nina Yuliana", "nina.smg@interport.com", "PR Creator"),
                ("Bagus Wicaksono", "bagus.smg@interport.com", "SES Approver")
            ]
            
            for name, email, role_name in employees:
                if role_name in role_map:
                    cur.execute(
                        "INSERT INTO user_assignments (full_name, email, entity_id, site_id, role_id, is_active) "
                        "VALUES (%s, %s, %s, %s, %s, TRUE) "
                        "ON CONFLICT (email, entity_id, role_id) DO UPDATE SET site_id = EXCLUDED.site_id, full_name = EXCLUDED.full_name",
                        (name, email, ilss_id, smg_id, role_map[role_name])
                    )

            print("Creating Workpaper ILSS-SMG...")
            cur.execute("SELECT id FROM work_units WHERE code = 'ILSS-SMG'")
            wu = cur.fetchone()
            if wu:
                cur.execute("DELETE FROM work_units WHERE id = %s", (wu["id"],))
            
            cur.execute(
                "INSERT INTO work_units (entity_id, site_id, code, project_name) VALUES (%s, %s, 'ILSS-SMG', 'SMG (Semarang)') RETURNING id",
                (ilss_id, smg_id)
            )
            wu_id = cur.fetchone()["id"]

            print("Setting up Workpaper Matrix...")
            cur.execute("SELECT id FROM amount_tiers ORDER BY seq")
            tier_ids = [t["id"] for t in cur.fetchall()]

            matrix_data = [
                ("President Director", "President Director SMG", "", [5, 6]),
                ("Director", "Director SMG", "", [4, 5]),
                ("Cost Reviewer", "Cost Control SMG", "Approval Khusus Cabang Semarang", [1, 2, 3, 4, 5, 6])
            ]
            
            seq = 1
            for role_name, pos_label, comment, checked_tiers in matrix_data:
                if role_name in role_map:
                    cur.execute(
                        "INSERT INTO workpaper_rows (work_unit_id, row_kind, seq, role_id, position, comment) VALUES (%s, 'authority', %s, %s, %s, %s) RETURNING id",
                        (wu_id, seq, role_map[role_name], pos_label, comment)
                    )
                    row_id = cur.fetchone()["id"]
                    
                    for t in checked_tiers:
                        if t <= len(tier_ids):
                            cur.execute("INSERT INTO workpaper_authority (row_id, tier_id, required) VALUES (%s, %s, TRUE)", (row_id, tier_ids[t-1]))
                    seq += 1
            
            if "Buyer" in role_map:
                cur.execute("INSERT INTO workpaper_rows (work_unit_id, row_kind, seq, role_id) VALUES (%s, 'buyer', 998, %s)", (wu_id, role_map["Buyer"]))
            if "PR Creator" in role_map:
                cur.execute("INSERT INTO workpaper_rows (work_unit_id, row_kind, seq, role_id) VALUES (%s, 'creator', 999, %s)", (wu_id, role_map["PR Creator"]))

            for t_id in tier_ids:
                if "PR Creator" in role_map and "SES Approver" in role_map:
                    cur.execute(
                        "INSERT INTO ses_entries (work_unit_id, tier_id, creator_role_id, approver_role_id) VALUES (%s, %s, %s, %s)",
                        (wu_id, t_id, role_map["PR Creator"], role_map["SES Approver"])
                    )

            conn.commit()
            print("Seed ILSS-SMG completed successfully!")

    except Exception as e:
        print(f"Error seeding database: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    seed_data()
