import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.db import get_db_connection

conn = get_db_connection()
try:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM work_units;")
        conn.commit()
        print("Deleted all workpapers successfully.")
except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
