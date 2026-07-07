from werkzeug.security import check_password_hash, generate_password_hash


def get_user_by_email(cur, email):
    cur.execute(
        "SELECT * FROM users WHERE lower(email) = lower(%s) AND is_active",
        (email,),
    )
    return cur.fetchone()


def verify_password(user, password):
    return bool(user) and check_password_hash(user["password_hash"], password)


def set_password(conn, user_id, new_password):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
            SET password_hash = %s, must_change_password = FALSE, updated_at = NOW()
            WHERE id = %s
            """,
            (generate_password_hash(new_password), user_id),
        )
