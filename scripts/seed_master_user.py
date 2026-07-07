import os
import sys
import psycopg2
from parse_master_user import parse
import re

def to_email(nama):
    # Basic sanitize name to email
    clean_name = re.sub(r'[^A-Za-z0-9\s]', '', nama)
    parts = [p.capitalize() for p in clean_name.split()]
    return ".".join(parts) + "@interport.co.id"

def main():
    if len(sys.argv) < 2:
        print("Usage: python seed_master_user.py <excel_file>")
        return
        
    path = sys.argv[1]
    ws, roster, hier = parse(path)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Missing DATABASE_URL in env")
        return

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        cur.execute("TRUNCATE user_assignments RESTART IDENTITY CASCADE;")
        
        # 1. Map existing entities
        cur.execute("SELECT code, id FROM entities;")
        entities_map = dict(cur.fetchall())
        
        # 2. Insert and map sites
        all_sites = set((r["entity"], r["site"]) for r in roster + hier)
        cur.execute("SELECT e.code, s.name, s.id FROM sites s JOIN entities e ON s.entity_id = e.id;")
        sites_map = {(row[0], row[1]): row[2] for row in cur.fetchall()}
        
        for entity_code, site_name in all_sites:
            entity_id = entities_map.get(entity_code)
            if not entity_id:
                cur.execute("INSERT INTO entities (code, name) VALUES (%s, %s) RETURNING id;", (entity_code, entity_code))
                entity_id = cur.fetchone()[0]
                entities_map[entity_code] = entity_id
            
            if (entity_code, site_name) not in sites_map:
                cur.execute("INSERT INTO sites (entity_id, name) VALUES (%s, %s) RETURNING id;", (entity_id, site_name))
                sites_map[(entity_code, site_name)] = cur.fetchone()[0]
                
        # 3. Insert and map roles
        all_roles = set(r["role"] for r in roster + hier)
        cur.execute("SELECT name, id FROM roles;")
        roles_map = dict(cur.fetchall())
        
        for role_name in all_roles:
            if role_name not in roles_map:
                cur.execute("INSERT INTO roles (name) VALUES (%s) RETURNING id;", (role_name,))
                roles_map[role_name] = cur.fetchone()[0]
                
        # 4. Insert user assignments
        inserted = 0
        skipped = 0
        
        # Helper to insert assignment
        def insert_assignment(nama, email, entity_code, site_name, role_name):
            nonlocal inserted, skipped
            entity_id = entities_map[entity_code]
            site_id = sites_map[(entity_code, site_name)]
            role_id = roles_map[role_name]
            
            try:
                cur.execute("""
                    INSERT INTO user_assignments (full_name, email, entity_id, site_id, role_id, is_active)
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (email, entity_id, role_id) DO NOTHING;
                """, (nama, email, entity_id, site_id, role_id))
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"Error inserting {nama} - {email} - {entity_code} - {role_name}: {e}")
                raise

        for r in roster:
            insert_assignment(r["nama"], r["email"] or to_email(r["nama"]), r["entity"], r["site"], r["role"])
            
        for h in hier:
            email = to_email(h["nama"])
            insert_assignment(h["nama"], email, h["entity"], h["site"], h["role"])

        conn.commit()
        print(f"Successfully seeded {inserted} user assignments (skipped {skipped} duplicate roles for same entity/email).")

    except Exception as e:
        conn.rollback()
        print("Seeding failed:", e)
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
