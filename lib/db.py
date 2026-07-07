import os

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)


def log_change(conn, admin_name, action, entity_code=None, field_changed=None,
                old_value=None, new_value=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO changelog
                (admin_name, action, entity_code, field_changed, old_value, new_value)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (admin_name, action, entity_code, field_changed, old_value, new_value),
        )
