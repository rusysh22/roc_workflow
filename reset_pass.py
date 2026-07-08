
import psycopg2, os
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
load_dotenv()
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
new_pass = generate_password_hash('Password123!')
cur.execute('UPDATE users SET password_hash = %s WHERE lower(email) = lower(%s)', (new_pass, 'Muhammad.Shubkhi@interport.co.id'))
conn.commit()
print('Password reset successful', cur.rowcount)

