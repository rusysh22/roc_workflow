import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

def get_schema(table):
    cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table}';")
    print(f"--- {table} ---")
    for r in cur.fetchall():
        print(r)

get_schema("sites")
get_schema("tier_entities")
get_schema("tier_sites")
