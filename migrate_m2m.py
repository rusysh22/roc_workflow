import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])

try:
    with conn.cursor() as cur:
        # 1. Create junction table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS entity_sites (
                entity_id INTEGER REFERENCES entities(id) ON DELETE CASCADE,
                site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
                PRIMARY KEY (entity_id, site_id)
            );
        """)
        
        # 2. Check if sites still has entity_id
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='sites' AND column_name='entity_id'")
        has_entity_id = cur.fetchone() is not None
        
        if has_entity_id:
            print("Migrating to Many-to-Many...")
            # 3. Migrate existing relations to junction table
            cur.execute("INSERT INTO entity_sites (entity_id, site_id) SELECT entity_id, id FROM sites WHERE entity_id IS NOT NULL ON CONFLICT DO NOTHING;")
            
            # 4. Deduplicate sites
            cur.execute("SELECT name, array_agg(id) FROM sites GROUP BY name HAVING count(*) > 1")
            duplicates = cur.fetchall()
            for name, ids in duplicates:
                keep_id = ids[0]
                merge_ids = tuple(ids[1:])
                
                print(f"Merging duplicate site '{name}' (IDs {merge_ids} -> {keep_id})")
                
                # Clean conflicts first
                cur.execute("""
                    DELETE FROM tier_sites ts1
                    WHERE site_id IN %s 
                    AND EXISTS (SELECT 1 FROM tier_sites ts2 WHERE ts2.site_id = %s AND ts1.tier_id = ts2.tier_id)
                """, (merge_ids, keep_id))
                cur.execute(f"UPDATE tier_sites SET site_id = %s WHERE site_id IN %s", (keep_id, merge_ids))

                cur.execute("""
                    DELETE FROM entity_sites es1
                    WHERE site_id IN %s 
                    AND EXISTS (SELECT 1 FROM entity_sites es2 WHERE es2.site_id = %s AND es1.entity_id = es2.entity_id)
                """, (merge_ids, keep_id))
                cur.execute(f"UPDATE entity_sites SET site_id = %s WHERE site_id IN %s", (keep_id, merge_ids))

                cur.execute(f"UPDATE user_assignments SET site_id = %s WHERE site_id IN %s", (keep_id, merge_ids))
                cur.execute(f"UPDATE work_units SET site_id = %s WHERE site_id IN %s", (keep_id, merge_ids))
                
                # Delete duplicate sites
                cur.execute(f"DELETE FROM sites WHERE id IN %s", (merge_ids,))
            
            # 5. Drop entity_id column from sites
            cur.execute("ALTER TABLE sites DROP COLUMN entity_id;")
            
            print("Migration completed successfully.")
        else:
            print("Migration already applied.")
            
    conn.commit()
except Exception as e:
    conn.rollback()
    print(f"Error: {e}")
finally:
    conn.close()
