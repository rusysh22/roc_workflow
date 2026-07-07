import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.db import get_db_connection

conn = get_db_connection()
try:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE work_units ADD COLUMN IF NOT EXISTS site_id INTEGER REFERENCES sites(id);")
        cur.execute("ALTER TABLE ses_entries ADD COLUMN IF NOT EXISTS creator_role_id INTEGER REFERENCES roles(id);")
        cur.execute("ALTER TABLE ses_entries ADD COLUMN IF NOT EXISTS approver_role_id INTEGER REFERENCES roles(id);")
        cur.execute("ALTER TABLE workpaper_rows ADD COLUMN IF NOT EXISTS position VARCHAR(255);")
        cur.execute("ALTER TABLE workpaper_rows ADD COLUMN IF NOT EXISTS comment TEXT;")
        conn.commit()
        print("Schema updated successfully (added site_id, roles, position, and comment).")
except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
