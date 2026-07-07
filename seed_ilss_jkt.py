import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.db import get_db_connection

def seed_data():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Clear existing data
            print("Clearing old data...")
            cur.execute("TRUNCATE TABLE ses_entries, workpaper_authority, workpaper_rows, work_units, user_assignments, roles, sites, entities, amount_tiers RESTART IDENTITY CASCADE;")

            # 2. Setup Entities and Sites
            print("Seeding Entities & Sites...")
            cur.execute("INSERT INTO entities (code, name, exchange_rate_idr) VALUES ('ILSS', 'PT INTERPORT LOGISTIC & SUPPORT SERVICES', 16000) RETURNING id")
            ilss_id = cur.fetchone()["id"]
            
            cur.execute("INSERT INTO sites (entity_id, name) VALUES (%s, 'HEAD OFFICE - JAKARTA') RETURNING id", (ilss_id,))
            jkt_id = cur.fetchone()["id"]

            # 3. Setup Amount Tiers
            print("Seeding Amount Tiers...")
            tiers = [
                (1, 0, 1000),
                (2, 1000, 5000),
                (3, 5000, 15000),
                (4, 15000, 80000),
                (5, 80000, 200000),
                (6, 200000, 2000000)
            ]
            tier_ids = []
            for seq, min_usd, max_usd in tiers:
                cur.execute("INSERT INTO amount_tiers (seq, min_usd, max_usd) VALUES (%s, %s, %s) RETURNING id", (seq, min_usd, max_usd))
                tier_ids.append(cur.fetchone()["id"])

            # 4. Setup Roles
            print("Seeding Roles...")
            role_names = [
                "President Director", "Director", "Deputy Director", "Assignment Director", 
                "Cost Reviewer", "Buyer", "PR Creator", "SES Approver"
            ]
            role_map = {}
            for name in role_names:
                cur.execute("INSERT INTO roles (name, is_centralized) VALUES (%s, FALSE) RETURNING id", (name,))
                role_map[name] = cur.fetchone()["id"]

            # 5. Setup Master Data Employees (user_assignments)
            print("Seeding Master Data Employees...")
            employees = [
                ("Adi Darma Shima", "adi@interport.com", "President Director"),
                ("Yukki Nugrahawan Hanafi", "yukki@interport.com", "Director"),
                ("Restrimaya Susiwi", "restrimaya@interport.com", "Deputy Director"),
                ("Iman Gandi Mihardja", "iman@interport.com", "Assignment Director"),
                ("Aji Darmadi", "aji@interport.com", "Assignment Director"),
                ("Dian Mei Fallahati", "dian@interport.com", "Assignment Director"),
                # Multiple Cost Reviewers! This demonstrates the dynamic aggregation perfectly.
                ("Adhitya Syafta", "adhitya@interport.com", "Cost Reviewer"),
                ("Rini Yurnalis", "rini@interport.com", "Cost Reviewer"),
                ("Engelika Panggabean", "engelika@interport.com", "Cost Reviewer"),
                ("Rasyid Arya Putra", "rasyid@interport.com", "Cost Reviewer"),
                # Multiple Buyers
                ("Yohanes K. Gatot Mulyono T", "yohanes@interport.com", "Buyer"),
                ("Basuki Wibawa", "basuki@interport.com", "Buyer"),
                ("Eko Santoso", "eko@interport.com", "Buyer"),
                ("Pawestri Wulan Ramadani", "pawestri@interport.com", "Buyer"),
                ("Itjie Tandi Karang", "itjie@interport.com", "Buyer"),
                ("Tirsa Awuy", "tirsa@interport.com", "Buyer"),
                # PR/SES Creators
                ("Oktavia Lija Setyana Situmeang", "oktavia@interport.com", "PR Creator"),
                ("Azizi Syaldira Vitricia", "azizi@interport.com", "PR Creator"),
                # SES Approver
                ("Annisa Maulydia", "annisa@interport.com", "SES Approver")
            ]
            
            for name, email, role_name in employees:
                cur.execute(
                    "INSERT INTO user_assignments (full_name, email, entity_id, site_id, role_id, is_active) VALUES (%s, %s, %s, %s, %s, TRUE)",
                    (name, email, ilss_id, jkt_id, role_map[role_name])
                )

            # 6. Create the Workpaper Unit
            print("Creating Workpaper Matrix...")
            cur.execute(
                "INSERT INTO work_units (entity_id, site_id, code, project_name) VALUES (%s, %s, 'ILSS-JKT', 'HEAD OFFICE - JAKARTA') RETURNING id",
                (ilss_id, jkt_id)
            )
            wu_id = cur.fetchone()["id"]

            # 7. Add Matrix Rows & Authority Checkboxes
            # Format: (Role Name, Position Label, Comment, Tiers that are checked [1-6])
            matrix_data = [
                ("President Director", "President Director", "", [6]),
                ("Director", "Vice President", "", [5, 6]),
                ("Deputy Director", "GCP Business Deputy Director", "", [4, 5, 6]),
                ("Assignment Director", "GCP Business Development Director", "", [1, 2, 3, 4, 5, 6]),
                ("Assignment Director", "GCP Operation Director", "", [1, 2, 3, 4, 5, 6]),
                ("Assignment Director", "GCP Support Director", "", [1, 2, 3, 4, 5, 6]),
                ("Cost Reviewer", "Cost Control", "$1 approval - all cost control at head office", [1, 2, 3, 4, 5, 6])
            ]
            
            seq = 1
            for role_name, pos_label, comment, checked_tiers in matrix_data:
                cur.execute(
                    "INSERT INTO workpaper_rows (work_unit_id, row_kind, seq, role_id, position, comment) VALUES (%s, 'authority', %s, %s, %s, %s) RETURNING id",
                    (wu_id, seq, role_map[role_name], pos_label, comment)
                )
                row_id = cur.fetchone()["id"]
                
                for t in checked_tiers:
                    cur.execute("INSERT INTO workpaper_authority (row_id, tier_id, required) VALUES (%s, %s, TRUE)", (row_id, tier_ids[t-1]))
                seq += 1
            
            # Buyer and PR Creator roles for the workpaper
            cur.execute("INSERT INTO workpaper_rows (work_unit_id, row_kind, seq, role_id) VALUES (%s, 'buyer', 998, %s)", (wu_id, role_map["Buyer"]))
            cur.execute("INSERT INTO workpaper_rows (work_unit_id, row_kind, seq, role_id) VALUES (%s, 'creator', 999, %s)", (wu_id, role_map["PR Creator"]))

            # 8. Setup SES Entries
            for t_id in tier_ids:
                cur.execute(
                    "INSERT INTO ses_entries (work_unit_id, tier_id, creator_role_id, approver_role_id) VALUES (%s, %s, %s, %s)",
                    (wu_id, t_id, role_map["PR Creator"], role_map["SES Approver"])
                )

            conn.commit()
            print("Seed ILSS-JKT completed successfully!")

    except Exception as e:
        print(f"Error seeding database: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    seed_data()
