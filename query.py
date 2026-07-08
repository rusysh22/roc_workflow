
import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute('SELECT id, full_name, email, is_active FROM users')
users = cur.fetchall()
for u in users:
    print(u)

