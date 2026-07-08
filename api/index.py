import os
import sys
import json
import urllib.parse
import urllib.request
from datetime import datetime

import psycopg2
from markupsafe import Markup, escape
from psycopg2.extras import Json
from werkzeug.security import generate_password_hash
from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, session, url_for

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.auth import get_user_by_email, set_password, verify_password  # noqa: E402
from lib.db import get_db_connection, log_change  # noqa: E402
from lib.export import build_export_workbook  # noqa: E402
from lib.money import format_tier_label  # noqa: E402

app = Flask(__name__, template_folder="../templates", static_folder="../static")
app.secret_key = os.environ.get("SECRET_KEY", "dev")

# --- MS SSO Config ---
MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.environ.get("MS_CLIENT_SECRET", "")
MS_TENANT_ID = os.environ.get("MS_TENANT_ID", "common")

# --- Auto-migrate DB Schema ---
try:
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE work_units ADD COLUMN IF NOT EXISTS site_id INTEGER REFERENCES sites(id);")
        cur.execute("ALTER TABLE ses_entries ADD COLUMN IF NOT EXISTS creator_role_id INTEGER REFERENCES roles(id);")
        cur.execute("ALTER TABLE ses_entries ADD COLUMN IF NOT EXISTS approver_role_id INTEGER REFERENCES roles(id);")
        cur.execute("ALTER TABLE workpaper_rows ADD COLUMN IF NOT EXISTS comment TEXT;")

        # RBAC: super_admin (full access) / manager (full access minus user management)
        # / portal_user (read-only Workpapers + Assignments only). Existing accounts
        # default to super_admin so no one already logged in gets locked out.
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'super_admin';")

        # Version & Status columns
        cur.execute("ALTER TABLE work_units ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft';")
        cur.execute("ALTER TABLE work_units ADD COLUMN IF NOT EXISTS version_major INTEGER NOT NULL DEFAULT 1;")
        cur.execute("ALTER TABLE work_units ADD COLUMN IF NOT EXISTS version_minor INTEGER NOT NULL DEFAULT 0;")
        cur.execute("ALTER TABLE work_units ADD COLUMN IF NOT EXISTS version_patch INTEGER NOT NULL DEFAULT 0;")

        # Version log (changelog per workpaper)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS workpaper_version_log (
                id SERIAL PRIMARY KEY,
                work_unit_id INTEGER REFERENCES work_units(id) ON DELETE CASCADE,
                version TEXT NOT NULL,
                bump_type TEXT NOT NULL,
                comment TEXT,
                author TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("ALTER TABLE workpaper_version_log ADD COLUMN IF NOT EXISTS snapshot JSONB;")
        cur.execute("ALTER TABLE workpaper_version_log ADD COLUMN IF NOT EXISTS changes JSONB;")

        # Assignment changes queue up here first; a version bump sweeps them
        # into that version's `changes` and clears the queue.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS workpaper_pending_changes (
                id SERIAL PRIMARY KEY,
                work_unit_id INTEGER REFERENCES work_units(id) ON DELETE CASCADE,
                section TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                author TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS tier_entities (
                tier_id INTEGER REFERENCES amount_tiers(id) ON DELETE CASCADE,
                entity_id INTEGER REFERENCES entities(id) ON DELETE CASCADE,
                PRIMARY KEY (tier_id, entity_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tier_sites (
                tier_id INTEGER REFERENCES amount_tiers(id) ON DELETE CASCADE,
                site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
                PRIMARY KEY (tier_id, site_id)
            )
        """)
        
        conn.commit()
    conn.close()
except Exception as e:
    print(f"Auto-migrate failed: {e}")

PUBLIC_ENDPOINTS = {"login", "static", "login_microsoft", "login_microsoft_callback"}

# RBAC: super_admin has full access. manager has full access except managing
# user accounts. portal_user is read-only, limited to Workpapers + Assignments
# (mutating routes below simply aren't in this set, so they're blocked outright).
PORTAL_USER_ALLOWED_ENDPOINTS = {
    "workpapers", "workpaper_detail", "workpaper_version_snapshot",
    "assignments", "logout", "change_password", "profile",
}
MANAGER_RESTRICTED_ENDPOINTS = {"users_list", "new_user", "edit_user", "delete_user"}


def _safe_next(path):
    """Only allow same-site relative redirects (blocks open-redirect via ?next=)."""
    if path and path.startswith("/") and not path.startswith("//"):
        return path
    return None


def _landing_page_for_role(role):
    return url_for("workpapers") if role == "portal_user" else url_for("home")


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if not session.get("user_id"):
        return redirect(url_for("login", next=request.path))
    if session.get("must_change_password") and request.endpoint != "change_password":
        return redirect(url_for("change_password"))

    role = session.get("role", "super_admin")
    if role == "portal_user" and request.endpoint not in PORTAL_USER_ALLOWED_ENDPOINTS:
        flash("Your account has read-only access to Workpapers and Assignments only.", "error")
        return redirect(url_for("workpapers"))
    if role == "manager" and request.endpoint in MANAGER_RESTRICTED_ENDPOINTS:
        flash("Only Super Admins can manage user accounts.", "error")
        return redirect(url_for("home"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        next_path = request.form.get("next") or ""

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                user = get_user_by_email(cur, email)
        finally:
            conn.close()

        if not verify_password(user, password):
            flash("Incorrect email or password.", "error")
            return render_template("login.html", email=email, next=next_path)

        session.clear()
        session["user_id"] = user["id"]
        session["full_name"] = user["full_name"]
        session["email"] = user["email"]
        session["role"] = user["role"]
        session["must_change_password"] = user["must_change_password"]
        return redirect(_safe_next(next_path) or _landing_page_for_role(user["role"]))

    return render_template("login.html", email="", next=request.args.get("next", ""))


@app.route("/login/microsoft")
def login_microsoft():
    if not MS_CLIENT_ID:
        flash("SSO has not been configured by the Administrator.", "error")
        return redirect(url_for("login"))
    
    auth_url = f"https://login.microsoftonline.com/{MS_TENANT_ID}/oauth2/v2.0/authorize"
    redirect_uri = url_for("login_microsoft_callback", _external=True)
    
    params = {
        "client_id": MS_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": "openid email profile",
    }
    
    url = auth_url + "?" + urllib.parse.urlencode(params)
    return redirect(url)

@app.route("/login/microsoft/callback")
def login_microsoft_callback():
    code = request.args.get("code")
    if not code:
        flash("Failed to log in with Microsoft.", "error")
        return redirect(url_for("login"))
    
    token_url = f"https://login.microsoftonline.com/{MS_TENANT_ID}/oauth2/v2.0/token"
    redirect_uri = url_for("login_microsoft_callback", _external=True)
    
    data = urllib.parse.urlencode({
        "client_id": MS_CLIENT_ID,
        "client_secret": MS_CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(token_url, data=data)
        with urllib.request.urlopen(req) as response:
            token_response = json.loads(response.read())
            
        access_token = token_response.get("access_token")
        
        # Get user info
        graph_url = "https://graph.microsoft.com/v1.0/me"
        req_me = urllib.request.Request(graph_url)
        req_me.add_header("Authorization", f"Bearer {access_token}")
        
        with urllib.request.urlopen(req_me) as response_me:
            user_info = json.loads(response_me.read())
            
        email = user_info.get("mail") or user_info.get("userPrincipalName")
        
        if not email:
            flash("Could not read the email profile from the Microsoft account.", "error")
            return redirect(url_for("login"))
            
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                user = get_user_by_email(cur, email)
        finally:
            conn.close()
            
        if not user:
            flash(f"SSO account ({email}) is not registered in the system. Contact your Admin.", "error")
            return redirect(url_for("login"))
            
        session.clear()
        session["user_id"] = user["id"]
        session["full_name"] = user["full_name"]
        session["email"] = user["email"]
        session["role"] = user["role"]
        session["must_change_password"] = user["must_change_password"]

        # Optionally handle 'next' state if passed via auth flow, omitted here for simplicity
        return redirect(_landing_page_for_role(user["role"]))
        
    except Exception as e:
        print("SSO Error:", str(e))
        flash("Failed to verify SSO login.", "error")
        return redirect(url_for("login"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/account/password", methods=["GET", "POST"])
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                user = get_user_by_email(cur, session["email"])

            if not verify_password(user, current_password):
                flash("Current password is incorrect.", "error")
            elif len(new_password) < 8:
                flash("New password must be at least 8 characters.", "error")
            elif new_password != confirm_password:
                flash("Password confirmation does not match.", "error")
            else:
                set_password(conn, user["id"], new_password)
                conn.commit()
                session["must_change_password"] = False
                flash("Password changed successfully.", "success")
                return redirect(_landing_page_for_role(session.get("role")))
        finally:
            conn.close()

    return render_template("change_password.html", forced=session.get("must_change_password", False))


# ---------- Users (Master Data, Super Admin only) ----------

USER_ROLES = ["super_admin", "manager", "portal_user"]
USER_ROLE_LABELS = {"super_admin": "Super Admin", "manager": "Manager", "portal_user": "Portal User"}


@app.route("/users")
def users_list():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, full_name, role, is_active, must_change_password, created_at "
                "FROM users ORDER BY full_name"
            )
            users = cur.fetchall()
    finally:
        conn.close()
    return render_template("users.html", users=users, role_labels=USER_ROLE_LABELS)


@app.route("/users/new", methods=["GET", "POST"])
def new_user():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        role = request.form.get("role") or "portal_user"
        password = request.form.get("password", "")

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not email:
            errors.append("Email is required.")
        if role not in USER_ROLES:
            errors.append("Invalid role.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")

        if not errors:
            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO users (email, full_name, password_hash, role, must_change_password)
                           VALUES (%s, %s, %s, %s, TRUE)""",
                        (email, full_name, generate_password_hash(password), role),
                    )
                    log_change(
                        conn, session["full_name"], "CREATE", entity_code=None,
                        field_changed="User", old_value=None,
                        new_value=f"{full_name} ({USER_ROLE_LABELS.get(role, role)})",
                    )
                    conn.commit()
                    flash("User created successfully.", "success")
                    return redirect(url_for("users_list"))
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                flash("A user with this email already exists.", "error")
            finally:
                conn.close()
        else:
            for message in errors:
                flash(message, "error")

    return render_template("user_form.html", mode="create", user=None, roles=USER_ROLES, role_labels=USER_ROLE_LABELS)


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
def edit_user(user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
            if not user:
                flash("User not found.", "error")
                return redirect(url_for("users_list"))

            if request.method == "POST":
                full_name = request.form.get("full_name", "").strip()
                email = request.form.get("email", "").strip()
                role = request.form.get("role") or user["role"]
                new_password = request.form.get("password", "")
                is_active = "is_active" in request.form

                errors = []
                if not full_name:
                    errors.append("Full name is required.")
                if not email:
                    errors.append("Email is required.")
                if role not in USER_ROLES:
                    errors.append("Invalid role.")
                if new_password and len(new_password) < 8:
                    errors.append("Password must be at least 8 characters.")
                if user_id == session.get("user_id") and not is_active:
                    errors.append("You cannot deactivate your own account.")
                if user_id == session.get("user_id") and role != "super_admin" and user["role"] == "super_admin":
                    errors.append("You cannot remove your own Super Admin role.")

                if not errors:
                    try:
                        if new_password:
                            cur.execute(
                                """UPDATE users SET full_name=%s, email=%s, role=%s, is_active=%s,
                                   password_hash=%s, must_change_password=TRUE, updated_at=NOW() WHERE id=%s""",
                                (full_name, email, role, is_active, generate_password_hash(new_password), user_id),
                            )
                        else:
                            cur.execute(
                                """UPDATE users SET full_name=%s, email=%s, role=%s, is_active=%s,
                                   updated_at=NOW() WHERE id=%s""",
                                (full_name, email, role, is_active, user_id),
                            )
                        if role != user["role"]:
                            log_change(
                                conn, session["full_name"], "UPDATE", entity_code=None,
                                field_changed="User role",
                                old_value=USER_ROLE_LABELS.get(user["role"], user["role"]),
                                new_value=USER_ROLE_LABELS.get(role, role),
                            )
                        conn.commit()
                        flash("User updated successfully.", "success")
                        return redirect(url_for("users_list"))
                    except psycopg2.errors.UniqueViolation:
                        conn.rollback()
                        flash("A user with this email already exists.", "error")
                else:
                    for message in errors:
                        flash(message, "error")
    finally:
        conn.close()

    return render_template("user_form.html", mode="edit", user=user, roles=USER_ROLES, role_labels=USER_ROLE_LABELS)


@app.route("/users/<int:user_id>/delete", methods=["POST"])
def delete_user(user_id):
    if user_id == session.get("user_id"):
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("users_list"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT full_name FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
            if user:
                cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
                log_change(
                    conn, session["full_name"], "DELETE", entity_code=None,
                    field_changed="User", old_value=user["full_name"], new_value=None,
                )
                conn.commit()
                flash("User deleted.", "success")
    finally:
        conn.close()
    return redirect(url_for("users_list"))

ENTITY_BADGE_COLORS = {
    "IMU": "blue",
    "ILSS": "teal",
    "IMN": "indigo",
    "IRB": "orange",
    "PGE": "green",
    "MBN": "purple",
    "ISB": "yellow",
    "INDIS": "gray",
    "KGTE": "pink",
}


def entity_badge_class(code):
    color = ENTITY_BADGE_COLORS.get(code, "gray")
    return f"bg-{color}-100 text-{color}-700"

def string_badge_class(val):
    if not val:
        return "bg-gray-100 text-gray-700"
    colors = ["red", "orange", "amber", "green", "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia", "pink", "rose"]
    idx = sum(ord(c) for c in val) % len(colors)
    color = colors[idx]
    return f"bg-{color}-100 text-{color}-700"

app.jinja_env.globals["entity_badge_class"] = entity_badge_class
app.jinja_env.globals["string_badge_class"] = string_badge_class


def bold_slash(value):
    """Bold the ' / ' separator between multiple names, keeping each name
    itself escaped (safe against XSS) since names are user-entered data."""
    if not value:
        return value
    parts = str(value).split(" / ")
    separator = Markup(' <span class="font-bold">/</span> ')
    return Markup(separator.join(escape(p) for p in parts))


app.jinja_env.filters["bold_slash"] = bold_slash


def get_lookup_data(cur):
    cur.execute("SELECT id, code, name FROM entities ORDER BY code")
    entities = cur.fetchall()

    cur.execute(
        """
        SELECT s.id, s.name, e.code AS entity_code, e.id AS entity_id
        FROM sites s
        JOIN entity_sites es ON es.site_id = s.id
        JOIN entities e ON e.id = es.entity_id
        ORDER BY e.code, s.name
        """
    )
    sites = cur.fetchall()

    cur.execute("SELECT id, name, is_centralized FROM roles ORDER BY name")
    roles = cur.fetchall()

    return entities, sites, roles


def get_employee_assignments_map(cur):
    """All assignments for every employee, grouped by email -- used to show
    'assigned elsewhere' info in the assignment form without extra requests."""
    cur.execute(
        """
        SELECT ua.id, ua.email, ua.is_active,
               e.code AS entity_code, s.name AS site_name, r.name AS role_name
        FROM user_assignments ua
        JOIN entities e ON e.id = ua.entity_id
        JOIN roles r ON r.id = ua.role_id
        LEFT JOIN sites s ON s.id = ua.site_id
        ORDER BY e.code, s.name, r.name
        """
    )
    by_email = {}
    for row in cur.fetchall():
        by_email.setdefault(row["email"], []).append({
            "id": row["id"],
            "entity_code": row["entity_code"],
            "site_name": row["site_name"],
            "role_name": row["role_name"],
            "is_active": row["is_active"],
        })
    return by_email


def get_affected_work_units(cur, entity_id, role_id):
    """Workpapers for this entity whose authority rows, buyer/creator rows, or
    SES entries reference this role -- i.e. workpapers whose displayed names
    would change if this role's assignments change."""
    if not entity_id or not role_id:
        return []
    cur.execute(
        """
        SELECT DISTINCT wu.id, wu.code
        FROM work_units wu
        WHERE wu.entity_id = %s
          AND (
            EXISTS (SELECT 1 FROM workpaper_rows wr WHERE wr.work_unit_id = wu.id AND wr.role_id = %s)
            OR EXISTS (
                SELECT 1 FROM ses_entries se
                WHERE se.work_unit_id = wu.id AND (se.creator_role_id = %s OR se.approver_role_id = %s)
            )
          )
        ORDER BY wu.code
        """,
        (entity_id, role_id, role_id, role_id),
    )
    return cur.fetchall()


def consume_pending_changes(cur, work_unit_id):
    """Fetch and clear all queued Assignment-driven changes for a workpaper,
    returning them as plain dicts to be folded into the version being bumped."""
    cur.execute(
        """SELECT section, field, old_value, new_value, author, created_at
           FROM workpaper_pending_changes WHERE work_unit_id = %s ORDER BY id""",
        (work_unit_id,),
    )
    rows = cur.fetchall()
    cur.execute("DELETE FROM workpaper_pending_changes WHERE work_unit_id = %s", (work_unit_id,))
    return [
        {"section": r["section"], "field": r["field"], "old": r["old_value"], "new": r["new_value"]}
        for r in rows
    ]


def queue_pending_changes(cur, entity_id, role_id, section, field, old_value, new_value, author):
    """Record a change against every workpaper this assignment affects. These
    sit in the queue until that workpaper's next version bump, at which point
    they're folded into that version's `changes` and cleared."""
    for unit in get_affected_work_units(cur, entity_id, role_id):
        cur.execute(
            """INSERT INTO workpaper_pending_changes
               (work_unit_id, section, field, old_value, new_value, author)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (unit["id"], section, field, old_value, new_value, author),
        )


def get_ho_jakarta_site_id(cur):
    cur.execute("SELECT id FROM sites WHERE name = %s LIMIT 1", ("HO Jakarta",))
    row = cur.fetchone()
    return row["id"] if row else None


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if request.method == "POST":
        flash("Profile updated successfully (Mock)", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")


@app.route("/")
def home():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Auto-migrate new columns for dynamic workpapers
            cur.execute("ALTER TABLE work_units ADD COLUMN IF NOT EXISTS site_id INTEGER REFERENCES sites(id);")
            cur.execute("ALTER TABLE ses_entries ADD COLUMN IF NOT EXISTS creator_role_id INTEGER REFERENCES roles(id);")
            cur.execute("ALTER TABLE ses_entries ADD COLUMN IF NOT EXISTS approver_role_id INTEGER REFERENCES roles(id);")
            conn.commit()

            cur.execute("SELECT COUNT(*) AS n FROM entities")
            total_entities = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM sites")
            total_sites = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM roles")
            total_roles = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM user_assignments")
            total_assignments = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM user_assignments WHERE is_active")
            total_active = cur.fetchone()["n"]

            cur.execute(
                """
                SELECT e.code AS entity_code, e.name AS entity_name,
                       COUNT(ua.id) AS total_count,
                       COUNT(ua.id) FILTER (WHERE ua.is_active) AS active_count,
                       COUNT(DISTINCT ua.role_id) FILTER (WHERE ua.is_active) AS roles_covered
                FROM entities e
                LEFT JOIN user_assignments ua ON ua.entity_id = e.id
                GROUP BY e.id, e.code, e.name
                ORDER BY e.code
                """
            )
            entity_summary = cur.fetchall()
            for row in entity_summary:
                pct = round(row["roles_covered"] / total_roles * 100) if total_roles else 0
                row["coverage_pct"] = pct
                row["coverage_tier"] = "good" if pct >= 70 else "warning" if pct >= 40 else "critical"

            cur.execute(
                """
                SELECT admin_name, action, entity_code, field_changed, old_value, new_value, changed_at
                FROM changelog
                ORDER BY changed_at DESC
                LIMIT 8
                """
            )
            recent_changes = cur.fetchall()
    finally:
        conn.close()

    return render_template(
        "index.html",
        total_entities=total_entities,
        total_sites=total_sites,
        total_roles=total_roles,
        total_assignments=total_assignments,
        total_active=total_active,
        entity_summary=entity_summary,
        recent_changes=recent_changes,
    )


# ---------- Assignments ----------

@app.route("/assignments")
def assignments():
    entity_code = request.args.get("entity") or ""
    role_id = request.args.get("role") or ""

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT ua.id, ua.full_name, ua.email, ua.is_active,
                       ua.entity_id,
                       e.code AS entity_code, e.name AS entity_name,
                       s.name AS site_name,
                       r.id AS role_id, r.name AS role_name, r.is_centralized
                FROM user_assignments ua
                JOIN entities e ON e.id = ua.entity_id
                JOIN roles r ON r.id = ua.role_id
                LEFT JOIN sites s ON s.id = ua.site_id
                WHERE 1=1
            """
            params = []
            if entity_code:
                query += " AND e.code = %s"
                params.append(entity_code)
            if role_id:
                query += " AND r.id = %s"
                params.append(role_id)
            query += " ORDER BY e.code, r.name, ua.full_name"
            cur.execute(query, params)
            rows = cur.fetchall()

            # Fix N+1 query issue by pre-fetching all work unit associations in one query
            cur.execute("""
                SELECT wu.entity_id, wr.role_id, wu.code
                FROM work_units wu
                JOIN workpaper_rows wr ON wr.work_unit_id = wu.id
                UNION
                SELECT wu.entity_id, se.creator_role_id AS role_id, wu.code
                FROM work_units wu
                JOIN ses_entries se ON se.work_unit_id = wu.id
                WHERE se.creator_role_id IS NOT NULL
                UNION
                SELECT wu.entity_id, se.approver_role_id AS role_id, wu.code
                FROM work_units wu
                JOIN ses_entries se ON se.work_unit_id = wu.id
                WHERE se.approver_role_id IS NOT NULL
            """)
            affected_map = {}
            for map_row in cur.fetchall():
                key = (map_row["entity_id"], map_row["role_id"])
                if key not in affected_map:
                    affected_map[key] = set()
                affected_map[key].add(map_row["code"])

            for row in rows:
                key = (row["entity_id"], row["role_id"])
                row["affected_workpapers"] = sorted(list(affected_map.get(key, [])))

            entities, _sites, roles = get_lookup_data(cur)
            cur.execute("SELECT DISTINCT email, full_name FROM user_assignments ORDER BY full_name")
            employees_list = cur.fetchall()
    finally:
        conn.close()

    return render_template(
        "assignments.html",
        assignments=rows,
        entities=entities,
        roles=roles,
        selected_entity=entity_code,
        selected_role=role_id,
        employees_list=employees_list,
    )


# ---------- Roles ----------
@app.route("/roles")
def roles_list():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM roles ORDER BY name")
            roles = cur.fetchall()
    finally:
        conn.close()
    return render_template("roles.html", roles=roles)

@app.route("/roles/new", methods=["GET", "POST"])
def new_role():
    if request.method == "POST":
        name = request.form.get("name")
        is_centralized = request.form.get("is_centralized") == "on"
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO roles (name, is_centralized) VALUES (%s, %s)",
                    (name, is_centralized)
                )
                conn.commit()
            flash("Role created successfully.", "success")
            return redirect(url_for("roles_list"))
        except Exception as e:
            flash(f"Error: {e}", "error")
        finally:
            conn.close()
    return render_template("role_form.html", role=None)

@app.route("/roles/edit/<int:id>", methods=["GET", "POST"])
def edit_role(id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if request.method == "POST":
                name = request.form.get("name")
                is_centralized = request.form.get("is_centralized") == "on"
                cur.execute(
                    "UPDATE roles SET name = %s, is_centralized = %s WHERE id = %s",
                    (name, is_centralized, id)
                )
                conn.commit()
                flash("Role updated successfully.", "success")
                return redirect(url_for("roles_list"))
            else:
                cur.execute("SELECT * FROM roles WHERE id = %s", (id,))
                role = cur.fetchone()
                if not role:
                    flash("Role not found.", "error")
                    return redirect(url_for("roles_list"))
    finally:
        conn.close()
    return render_template("role_form.html", role=role)

@app.route("/roles/delete/<int:id>", methods=["POST"])
def delete_role(id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM roles WHERE id = %s", (id,))
            conn.commit()
        flash("Role deleted successfully.", "success")
    except Exception as e:
        flash("Cannot delete this role because it is still assigned to users or workpapers.", "error")
    finally:
        conn.close()
    return redirect(url_for("roles_list"))


# ---------- Amount Tiers ----------
@app.route("/amount-tiers")
def amount_tiers_list():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.*, 
                       (SELECT COUNT(*) FROM tier_entities te WHERE te.tier_id = a.id) as entity_count,
                       (SELECT COUNT(*) FROM tier_sites ts WHERE ts.tier_id = a.id) as site_count,
                       (SELECT COUNT(*) FROM entities) as total_entities,
                       (SELECT COUNT(*) FROM sites) as total_sites
                FROM amount_tiers a
                ORDER BY a.seq
            """)
            tiers = cur.fetchall()
    finally:
        conn.close()
    return render_template("amount_tiers.html", tiers=tiers)

@app.route("/amount-tiers/new", methods=["GET", "POST"])
def new_amount_tier():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if request.method == "POST":
                seq = request.form.get("seq", type=int)
                min_usd = request.form.get("min_usd", type=float)
                max_usd = request.form.get("max_usd", type=float) or None
                entity_ids = request.form.getlist("entity_ids")
                site_ids = request.form.getlist("site_ids")
                
                try:
                    cur.execute("""
                        INSERT INTO amount_tiers (seq, min_usd, max_usd)
                        VALUES (%s, %s, %s) RETURNING id
                    """, (seq, min_usd, max_usd))
                    new_id = cur.fetchone()["id"]
                    
                    for eid in entity_ids:
                        cur.execute("INSERT INTO tier_entities (tier_id, entity_id) VALUES (%s, %s)", (new_id, eid))
                    for sid in site_ids:
                        cur.execute("INSERT INTO tier_sites (tier_id, site_id) VALUES (%s, %s)", (new_id, sid))
                        

                    conn.commit()
                    flash("Limit added successfully.", "success")
                    return redirect(url_for("amount_tiers_list"))
                except Exception as e:
                    conn.rollback()
                    flash(f"Error: {e}", "error")
            
            entities, sites, _ = get_lookup_data(cur)
    finally:
        conn.close()
    return render_template("amount_tier_form.html", mode="create", tier={}, entities=entities, sites=sites)

@app.route("/amount-tiers/<int:tier_id>/edit", methods=["GET", "POST"])
def edit_amount_tier(tier_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if request.method == "POST":
                seq = request.form.get("seq", type=int)
                min_usd = request.form.get("min_usd", type=float)
                max_usd = request.form.get("max_usd", type=float) or None
                entity_ids = request.form.getlist("entity_ids")
                site_ids = request.form.getlist("site_ids")
                
                try:
                    cur.execute("""
                        UPDATE amount_tiers
                        SET seq=%s, min_usd=%s, max_usd=%s
                        WHERE id=%s
                    """, (seq, min_usd, max_usd, tier_id))
                    
                    cur.execute("DELETE FROM tier_entities WHERE tier_id = %s", (tier_id,))
                    cur.execute("DELETE FROM tier_sites WHERE tier_id = %s", (tier_id,))
                    
                    for eid in entity_ids:
                        cur.execute("INSERT INTO tier_entities (tier_id, entity_id) VALUES (%s, %s)", (tier_id, eid))
                    for sid in site_ids:
                        cur.execute("INSERT INTO tier_sites (tier_id, site_id) VALUES (%s, %s)", (tier_id, sid))
                        
                    conn.commit()
                    flash("Limit updated successfully.", "success")
                    return redirect(url_for("amount_tiers_list"))
                except Exception as e:
                    conn.rollback()
                    flash(f"Error: {e}", "error")
            
            cur.execute("SELECT * FROM amount_tiers WHERE id = %s", (tier_id,))
            tier = cur.fetchone()
            if not tier:
                return redirect(url_for("amount_tiers_list"))
                
            cur.execute("SELECT entity_id FROM tier_entities WHERE tier_id = %s", (tier_id,))
            tier_entities = [row["entity_id"] for row in cur.fetchall()]
            
            cur.execute("SELECT site_id FROM tier_sites WHERE tier_id = %s", (tier_id,))
            tier_sites = [row["site_id"] for row in cur.fetchall()]
            
            entities, sites, _ = get_lookup_data(cur)
    finally:
        conn.close()
    return render_template("amount_tier_form.html", mode="edit", tier=tier, entities=entities, sites=sites, tier_entities=tier_entities, tier_sites=tier_sites)

@app.route("/amount-tiers/<int:tier_id>/delete", methods=["POST"])
def delete_amount_tier(tier_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM amount_tiers WHERE id = %s", (tier_id,))
            conn.commit()
            flash("Limit deleted successfully.", "success")
    except Exception as e:
        flash(f"Failed to delete: {e}", "error")
    finally:
        conn.close()
    return redirect(url_for("amount_tiers_list"))


# ---------- Employees (Master Data) ----------

@app.route("/employees")
def employees():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    ua.email,
                    MAX(ua.full_name) as full_name,
                    array_agg(
                        concat(
                            COALESCE(e.code, '-'), ' : ',
                            COALESCE(s.name, 'All Site/Unit Applied'), ' — ',
                            COALESCE(r.name, '-')
                        ) ORDER BY e.code, s.name, r.name
                    ) as assignments_list,
                    bool_or(ua.is_active) as is_active
                FROM user_assignments ua
                JOIN entities e ON e.id = ua.entity_id
                JOIN roles r ON r.id = ua.role_id
                LEFT JOIN sites s ON s.id = ua.site_id
                GROUP BY ua.email
                ORDER BY full_name
            """)
            employees = cur.fetchall()
    finally:
        conn.close()
    return render_template("employees.html", employees=employees)


def _read_assignment_form(cur, roles):
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    entity_id = request.form.get("entity_id") or ""
    role_id = request.form.get("role_id") or ""
    site_id = request.form.get("site_id") or ""
    is_active = "is_active" in request.form

    errors = []
    if not full_name:
        errors.append("Full name is required.")
    if not email:
        errors.append("Email is required.")
    if not entity_id:
        errors.append("Entity is required.")
    if not role_id:
        errors.append("Role is required.")

    role_row = next((r for r in roles if str(r["id"]) == str(role_id)), None)
    if role_row and role_row["is_centralized"]:
        site_id = get_ho_jakarta_site_id(cur)
    elif not site_id:
        errors.append("Site is required.")

    return {
        "full_name": full_name,
        "email": email,
        "entity_id": entity_id,
        "role_id": role_id,
        "site_id": site_id,
        "is_active": is_active,
        "role_row": role_row,
    }, errors


def _sticky_assignment(data, extra=None):
    sticky = {
        "full_name": data["full_name"],
        "email": data["email"],
        "entity_id": int(data["entity_id"]) if data["entity_id"] else None,
        "role_id": int(data["role_id"]) if data["role_id"] else None,
        "site_id": int(data["site_id"]) if data["site_id"] else None,
        "is_active": data["is_active"],
    }
    if extra:
        sticky.update(extra)
    return sticky


@app.route("/assignments/new", methods=["GET", "POST"])
def new_assignment():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            entities, sites, roles = get_lookup_data(cur)
            cur.execute("SELECT DISTINCT email, full_name FROM user_assignments ORDER BY full_name")
            employees_list = cur.fetchall()
            employee_assignments = get_employee_assignments_map(cur)

            if request.method == "POST":
                data, errors = _read_assignment_form(cur, roles)

                if errors:
                    for message in errors:
                        flash(message, "error")
                    return render_template(
                        "assignment_form.html", mode="create", assignment=_sticky_assignment(data),
                        entities=entities, sites=sites, roles=roles, employees_list=employees_list,
                        employee_assignments=employee_assignments, current_assignment_id=None,
                    )

                try:
                    cur.execute(
                        """
                        INSERT INTO user_assignments (full_name, email, entity_id, site_id, role_id, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (data["full_name"], data["email"], data["entity_id"], data["site_id"],
                         data["role_id"], data["is_active"]),
                    )
                    entity_code = next(e["code"] for e in entities if e["id"] == int(data["entity_id"]))
                    role_name = data["role_row"]["name"] if data["role_row"] else ""
                    log_change(
                        conn, session["full_name"], "CREATE", entity_code=entity_code,
                        field_changed=None, old_value=None,
                        new_value=f"{data['full_name']} - {role_name}",
                    )
                    queue_pending_changes(
                        cur, int(data["entity_id"]), int(data["role_id"]),
                        section="Assignment", field=role_name,
                        old_value=None, new_value=f"{data['full_name']} assigned",
                        author=session["full_name"],
                    )
                    conn.commit()
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    flash("An assignment with this email, entity, and role already exists.", "error")
                    return render_template(
                        "assignment_form.html", mode="create", assignment=_sticky_assignment(data),
                        entities=entities, sites=sites, roles=roles, employees_list=employees_list,
                        employee_assignments=employee_assignments, current_assignment_id=None,
                    )

                flash("Assignment added.", "success")
                return redirect(url_for("assignments"))

            assignment = None
    finally:
        conn.close()

    return render_template(
        "assignment_form.html", mode="create", assignment=assignment,
        entities=entities, sites=sites, roles=roles, employees_list=employees_list,
        employee_assignments=employee_assignments, current_assignment_id=None,
    )

@app.route("/assignments/<int:assignment_id>/edit", methods=["GET", "POST"])
def edit_assignment(assignment_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            entities, sites, roles = get_lookup_data(cur)
            cur.execute("SELECT DISTINCT email, full_name FROM user_assignments ORDER BY full_name")
            employees_list = cur.fetchall()
            employee_assignments = get_employee_assignments_map(cur)

            cur.execute(
                """
                SELECT ua.*, e.code AS entity_code, s.name AS site_name, r.name AS role_name
                FROM user_assignments ua
                JOIN entities e ON e.id = ua.entity_id
                JOIN roles r ON r.id = ua.role_id
                LEFT JOIN sites s ON s.id = ua.site_id
                WHERE ua.id = %s
                """,
                (assignment_id,),
            )
            existing = cur.fetchone()
            if not existing:
                flash("Assignment not found.", "error")
                return redirect(url_for("assignments"))

            if request.method == "POST":
                data, errors = _read_assignment_form(cur, roles)

                if errors:
                    for message in errors:
                        flash(message, "error")
                    return render_template(
                        "assignment_form.html", mode="edit",
                        assignment=_sticky_assignment(data, {"id": assignment_id}),
                        entities=entities, sites=sites, roles=roles, employees_list=employees_list,
                        employee_assignments=employee_assignments, current_assignment_id=assignment_id,
                    )

                new_entity_code = next(e["code"] for e in entities if e["id"] == int(data["entity_id"]))
                new_role_name = data["role_row"]["name"] if data["role_row"] else ""
                new_site_name = next(
                    (s["name"] for s in sites if s["id"] == int(data["site_id"])), None,
                ) if data["site_id"] else None

                diffs = []
                if existing["full_name"] != data["full_name"]:
                    diffs.append(("full_name", existing["full_name"], data["full_name"]))
                if existing["email"] != data["email"]:
                    diffs.append(("email", existing["email"], data["email"]))
                if existing["entity_code"] != new_entity_code:
                    diffs.append(("entity", existing["entity_code"], new_entity_code))
                if (existing["site_name"] or None) != (new_site_name or None):
                    diffs.append(("site", existing["site_name"], new_site_name))
                if existing["role_name"] != new_role_name:
                    diffs.append(("role", existing["role_name"], new_role_name))
                if existing["is_active"] != data["is_active"]:
                    diffs.append(("is_active", existing["is_active"], data["is_active"]))

                try:
                    cur.execute(
                        """
                        UPDATE user_assignments
                        SET full_name = %s, email = %s, entity_id = %s, site_id = %s,
                            role_id = %s, is_active = %s, updated_at = NOW()
                        WHERE id = %s
                        """,
                        (data["full_name"], data["email"], data["entity_id"], data["site_id"],
                         data["role_id"], data["is_active"], assignment_id),
                    )
                    for field, old_value, new_value in diffs:
                        log_change(
                            conn, session["full_name"], "UPDATE", entity_code=new_entity_code,
                            field_changed=field,
                            old_value=str(old_value) if old_value is not None else None,
                            new_value=str(new_value) if new_value is not None else None,
                        )

                    if diffs:
                        diff_summary = "; ".join(f"{f}: {o or '-'} -> {n or '-'}" for f, o, n in diffs)
                        # Queue against the new (entity, role) -- where the assignment now lives.
                        queue_pending_changes(
                            cur, int(data["entity_id"]), int(data["role_id"]),
                            section="Assignment", field=new_role_name or "Assignment",
                            old_value=existing["full_name"], new_value=f"{data['full_name']} ({diff_summary})",
                            author=session["full_name"],
                        )
                        # If entity or role changed, the OLD workpapers this assignment
                        # used to affect need to know it no longer applies there too.
                        if existing["entity_id"] != int(data["entity_id"]) or existing["role_id"] != int(data["role_id"]):
                            queue_pending_changes(
                                cur, existing["entity_id"], existing["role_id"],
                                section="Assignment", field=existing["role_name"] or "Assignment",
                                old_value=f"{existing['full_name']} assigned", new_value="Reassigned elsewhere",
                                author=session["full_name"],
                            )

                    conn.commit()
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    flash("An assignment with this email, entity, and role already exists.", "error")
                    return render_template(
                        "assignment_form.html", mode="edit",
                        assignment=_sticky_assignment(data, {"id": assignment_id}),
                        entities=entities, sites=sites, roles=roles, employees_list=employees_list,
                        employee_assignments=employee_assignments, current_assignment_id=assignment_id,
                    )

                flash("Assignment updated.", "success")
                return redirect(url_for("assignments"))

            assignment = dict(existing)
    finally:
        conn.close()

    return render_template(
        "assignment_form.html", mode="edit", assignment=assignment,
        entities=entities, sites=sites, roles=roles, employees_list=employees_list,
        employee_assignments=employee_assignments, current_assignment_id=assignment_id,
    )


@app.route("/assignments/<int:assignment_id>/reassign", methods=["POST"])
def reassign_assignment(assignment_id):
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    if not full_name or not email:
        flash("Name and Email are required for reassign.", "error")
        return redirect(url_for("assignments"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ua.full_name, ua.entity_id, ua.role_id, e.code AS entity_code, r.name AS role_name
                FROM user_assignments ua
                JOIN entities e ON e.id = ua.entity_id
                JOIN roles r ON r.id = ua.role_id
                WHERE ua.id = %s
                """,
                (assignment_id,),
            )
            old_assignment = cur.fetchone()
            if not old_assignment:
                flash("Assignment not found.", "error")
                return redirect(url_for("assignments"))

            try:
                cur.execute(
                    "UPDATE user_assignments SET full_name = %s, email = %s WHERE id = %s",
                    (full_name, email, assignment_id)
                )
                log_change(
                    conn, session["full_name"], "UPDATE", entity_code=old_assignment["entity_code"],
                    field_changed=f"Reassign {old_assignment['role_name']}",
                    old_value=old_assignment["full_name"], new_value=full_name
                )
                queue_pending_changes(
                    cur, old_assignment["entity_id"], old_assignment["role_id"],
                    section="Assignment", field=old_assignment["role_name"] or "Assignment",
                    old_value=old_assignment["full_name"], new_value=full_name,
                    author=session["full_name"],
                )
                conn.commit()
                flash("Assignment successfully reassigned.", "success")
            except Exception as e:
                conn.rollback()
                flash("Cannot reassign. It is possible the user is already assigned to this role in this entity/site.", "error")
    finally:
        conn.close()
    return redirect(url_for("assignments"))


@app.route("/assignments/<int:assignment_id>/delete", methods=["POST"])
def delete_assignment(assignment_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ua.full_name, ua.entity_id, ua.role_id, e.code AS entity_code, r.name AS role_name
                FROM user_assignments ua
                JOIN entities e ON e.id = ua.entity_id
                JOIN roles r ON r.id = ua.role_id
                WHERE ua.id = %s
                """,
                (assignment_id,),
            )
            existing = cur.fetchone()
            if not existing:
                flash("Assignment not found.", "error")
                return redirect(url_for("assignments"))

            cur.execute("DELETE FROM user_assignments WHERE id = %s", (assignment_id,))
            log_change(
                conn, session["full_name"], "DELETE", entity_code=existing["entity_code"],
                field_changed=None,
                old_value=f"{existing['full_name']} - {existing['role_name']}",
                new_value=None,
            )
            queue_pending_changes(
                cur, existing["entity_id"], existing["role_id"],
                section="Assignment", field=existing["role_name"] or "Assignment",
                old_value=f"{existing['full_name']} assigned", new_value="Removed",
                author=session["full_name"],
            )
            conn.commit()
    finally:
        conn.close()

    flash("Assignment deleted.", "success")
    return redirect(url_for("assignments"))


# ---------- Workpapers ----------

@app.route("/workpapers")
def workpapers():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT wu.id, wu.code, wu.project_name, wu.site_id,
                       wu.status, wu.version_major, wu.version_minor, wu.version_patch,
                       e.code AS entity_code, e.name AS entity_name,
                       s.name AS site_name,
                       lvl.author AS last_modified_by, lvl.created_at AS last_modified_at,
                       COALESCE(pc.n, 0) AS pending_count
                FROM work_units wu
                JOIN entities e ON e.id = wu.entity_id
                LEFT JOIN sites s ON s.id = wu.site_id
                LEFT JOIN LATERAL (
                    SELECT author, created_at FROM workpaper_version_log
                    WHERE work_unit_id = wu.id ORDER BY id DESC LIMIT 1
                ) lvl ON true
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) AS n FROM workpaper_pending_changes WHERE work_unit_id = wu.id
                ) pc ON true
                ORDER BY e.code, wu.sort_order, wu.code
                """
            )
            units = cur.fetchall()
    finally:
        conn.close()

    grouped = {}
    for u in units:
        grouped.setdefault((u["entity_code"], u["entity_name"]), []).append(u)
    groups = [
        {"entity_code": k[0], "entity_name": k[1], "units": v}
        for k, v in grouped.items()
    ]

    return render_template("workpapers.html", groups=groups, total_units=len(units))


@app.route("/workpapers/<code>")
def workpaper_detail(code):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT wu.id, wu.code, wu.project_name, wu.entity_id, wu.site_id,
                       wu.status, wu.version_major, wu.version_minor, wu.version_patch,
                       e.code AS entity_code, e.name AS entity_name, e.exchange_rate_idr,
                       s.name AS site_name
                FROM work_units wu
                JOIN entities e ON e.id = wu.entity_id
                LEFT JOIN sites s ON s.id = wu.site_id
                WHERE wu.code = %s
                """,
                (code,),
            )
            unit = cur.fetchone()
            if not unit:
                flash("Workpaper not found.", "error")
                return redirect(url_for("workpapers"))

            cur.execute("""
                SELECT id, seq, min_usd, max_usd 
                FROM amount_tiers a
                WHERE EXISTS (SELECT 1 FROM tier_entities WHERE tier_id = a.id AND entity_id = %s)
                  AND (%s IS NULL OR EXISTS (SELECT 1 FROM tier_sites WHERE tier_id = a.id AND site_id = %s))
                ORDER BY seq
            """, (unit["entity_id"], unit["site_id"], unit["site_id"]))
            bands = [
                {
                    "id": t["id"],
                    "seq": t["seq"],
                    "label": format_tier_label(t["min_usd"], t["max_usd"], unit["exchange_rate_idr"]),
                }
                for t in cur.fetchall()
            ]

            # Fetch row structure
            cur.execute(
                """
                SELECT wr.id, wr.row_kind, wr.seq, wr.role_id, r.name AS level_label,
                       wr.person_name, wr.position, wr.comment
                FROM workpaper_rows wr
                LEFT JOIN roles r ON r.id = wr.role_id
                WHERE wr.work_unit_id = %s
                ORDER BY wr.seq
                """,
                (unit["id"],),
            )
            rows = cur.fetchall()
            
            # Fetch active employees for this entity and site
            # If site_id is missing, fallback to entity only
            if unit["site_id"]:
                cur.execute(
                    """
                    SELECT role_id, array_agg(DISTINCT full_name) as names 
                    FROM user_assignments 
                    WHERE is_active = TRUE AND entity_id = %s AND site_id = %s
                    GROUP BY role_id
                    """,
                    (unit["entity_id"], unit["site_id"])
                )
            else:
                cur.execute(
                    """
                    SELECT role_id, array_agg(DISTINCT full_name) as names 
                    FROM user_assignments 
                    WHERE is_active = TRUE AND entity_id = %s
                    GROUP BY role_id
                    """,
                    (unit["entity_id"],)
                )
            
            active_users_by_role = {r["role_id"]: r["names"] for r in cur.fetchall()}
            
            # Populate dynamic names for rows
            for row in rows:
                if row["role_id"] and row["role_id"] in active_users_by_role:
                    row["dynamic_names"] = " / ".join(active_users_by_role[row["role_id"]])
                else:
                    row["dynamic_names"] = row["person_name"] # fallback to hardcoded if no active assignment


            cur.execute(
                """
                SELECT wa.row_id, wa.tier_id, wa.required
                FROM workpaper_authority wa
                JOIN workpaper_rows wr ON wr.id = wa.row_id
                WHERE wr.work_unit_id = %s
                """,
                (unit["id"],),
            )
            matrix = {(m["row_id"], m["tier_id"]): m["required"] for m in cur.fetchall()}

            cur.execute(
                """
                SELECT tier_id, creator_name, approver_name, creator_role_id, approver_role_id
                FROM ses_entries
                WHERE work_unit_id = %s
                """,
                (unit["id"],),
            )
            ses = {s["tier_id"]: s for s in cur.fetchall()}
            
            # Populate SES dynamic names
            for tier_id, s in ses.items():
                if s["creator_role_id"] and s["creator_role_id"] in active_users_by_role:
                    s["dynamic_creator"] = " / ".join(active_users_by_role[s["creator_role_id"]])
                else:
                    s["dynamic_creator"] = s["creator_name"]
                
                if s["approver_role_id"] and s["approver_role_id"] in active_users_by_role:
                    s["dynamic_approver"] = " / ".join(active_users_by_role[s["approver_role_id"]])
                else:
                    s["dynamic_approver"] = s["approver_name"]

            cur.execute(
                "SELECT code, project_name, site_id FROM work_units ORDER BY sort_order, code"
            )
            all_units = cur.fetchall()

            cur.execute(
                """SELECT id, version, bump_type, comment, author, created_at,
                          (snapshot IS NOT NULL) AS has_snapshot,
                          jsonb_array_length(COALESCE(changes, '[]'::jsonb)) AS change_count
                   FROM workpaper_version_log WHERE work_unit_id=%s ORDER BY id DESC""",
                (unit["id"],)
            )
            version_log = cur.fetchall()

            cur.execute(
                """SELECT section, field, old_value, new_value, author, created_at
                   FROM workpaper_pending_changes WHERE work_unit_id=%s ORDER BY id""",
                (unit["id"],)
            )
            pending_changes = cur.fetchall()
    finally:
        conn.close()

    authority_rows = [r for r in rows if r["row_kind"] == "authority"]
    buyer_row = next((r for r in rows if r["row_kind"] == "buyer"), None)
    creator_row = next((r for r in rows if r["row_kind"] == "creator"), None)

    return render_template(
        "workpaper_detail.html",
        unit=unit, bands=bands, authority_rows=authority_rows,
        buyer_row=buyer_row, creator_row=creator_row,
        matrix=matrix, ses=ses, all_units=all_units, version_log=version_log,
        pending_changes=pending_changes,
    )


def build_workpaper_snapshot(cur, unit_id):
    """Freeze the current state of a workpaper into a self-contained JSON-able
    dict (no FK references) so it keeps rendering correctly even if the
    underlying roles/tiers/rows are later edited or deleted."""
    cur.execute(
        """
        SELECT wu.code, wu.project_name, wu.entity_id, wu.site_id,
               e.name AS entity_name, e.exchange_rate_idr,
               s.name AS site_name
        FROM work_units wu
        JOIN entities e ON e.id = wu.entity_id
        LEFT JOIN sites s ON s.id = wu.site_id
        WHERE wu.id = %s
        """,
        (unit_id,),
    )
    unit = cur.fetchone()

    cur.execute(
        """
        SELECT id, seq, min_usd, max_usd
        FROM amount_tiers a
        WHERE EXISTS (SELECT 1 FROM tier_entities WHERE tier_id = a.id AND entity_id = %s)
          AND (%s IS NULL OR EXISTS (SELECT 1 FROM tier_sites WHERE tier_id = a.id AND site_id = %s))
        ORDER BY seq
        """,
        (unit["entity_id"], unit["site_id"], unit["site_id"]),
    )
    bands = [
        {"id": t["id"], "label": format_tier_label(t["min_usd"], t["max_usd"], unit["exchange_rate_idr"])}
        for t in cur.fetchall()
    ]

    cur.execute(
        """
        SELECT wr.id, wr.row_kind, wr.role_id, wr.person_name, wr.comment,
               r.name AS level_label, wr.position
        FROM workpaper_rows wr
        LEFT JOIN roles r ON r.id = wr.role_id
        WHERE wr.work_unit_id = %s
        ORDER BY wr.seq
        """,
        (unit_id,),
    )
    rows = cur.fetchall()

    if unit["site_id"]:
        cur.execute(
            """
            SELECT role_id, array_agg(DISTINCT full_name) AS names
            FROM user_assignments
            WHERE is_active = TRUE AND entity_id = %s AND site_id = %s
            GROUP BY role_id
            """,
            (unit["entity_id"], unit["site_id"]),
        )
    else:
        cur.execute(
            """
            SELECT role_id, array_agg(DISTINCT full_name) AS names
            FROM user_assignments
            WHERE is_active = TRUE AND entity_id = %s
            GROUP BY role_id
            """,
            (unit["entity_id"],),
        )
    active_users_by_role = {r["role_id"]: r["names"] for r in cur.fetchall()}

    def resolve_name(row):
        if row["role_id"] and row["role_id"] in active_users_by_role:
            return " / ".join(active_users_by_role[row["role_id"]])
        return row["person_name"]

    cur.execute(
        """
        SELECT wa.row_id, wa.tier_id, wa.required
        FROM workpaper_authority wa
        JOIN workpaper_rows wr ON wr.id = wa.row_id
        WHERE wr.work_unit_id = %s
        """,
        (unit_id,),
    )
    matrix = {(m["row_id"], m["tier_id"]): m["required"] for m in cur.fetchall()}

    authority_rows = []
    buyer_name = None
    creator_name = None
    for r in rows:
        name = resolve_name(r)
        if r["row_kind"] == "authority":
            authority_rows.append({
                "level_label": r["level_label"],
                "name": name,
                "position": r["position"],
                "comment": r["comment"],
                "checks": [bool(matrix.get((r["id"], b["id"]))) for b in bands],
            })
        elif r["row_kind"] == "buyer":
            buyer_name = name
        elif r["row_kind"] == "creator":
            creator_name = name

    cur.execute(
        """
        SELECT tier_id, creator_name, approver_name, creator_role_id, approver_role_id
        FROM ses_entries
        WHERE work_unit_id = %s
        """,
        (unit_id,),
    )
    ses_by_tier = {s["tier_id"]: s for s in cur.fetchall()}

    ses_list = []
    for b in bands:
        s = ses_by_tier.get(b["id"])
        creator = approver = None
        if s:
            creator = (
                " / ".join(active_users_by_role[s["creator_role_id"]])
                if s["creator_role_id"] and s["creator_role_id"] in active_users_by_role
                else s["creator_name"]
            )
            approver = (
                " / ".join(active_users_by_role[s["approver_role_id"]])
                if s["approver_role_id"] and s["approver_role_id"] in active_users_by_role
                else s["approver_name"]
            )
        ses_list.append({"band_label": b["label"], "creator": creator, "approver": approver})

    return {
        "code": unit["code"],
        "project_name": unit["project_name"],
        "entity_name": unit["entity_name"],
        "site_name": unit["site_name"],
        "bands": [b["label"] for b in bands],
        "authority_rows": authority_rows,
        "buyer_name": buyer_name,
        "creator_name": creator_name,
        "ses": ses_list,
    }


def _next_workpaper_version(unit, bump_type):
    maj, minn, pat = unit["version_major"], unit["version_minor"], unit["version_patch"]
    if bump_type == "major":
        maj += 1; minn = 0; pat = 0
    elif bump_type == "minor":
        minn += 1; pat = 0
    elif bump_type == "patch":
        pat += 1
    return maj, minn, pat, f"{maj}.{minn}.{pat}"


def _band_short_label(band_label):
    """Compact 'US$15k - US$80k' style text from a format_tier_label() dict,
    for use in human-readable change descriptions."""
    if not band_label:
        return "?"
    usd = band_label.get("usd", {})
    if usd.get("op") == "<":
        return f"< {usd.get('max')}"
    if usd.get("op") == ">=":
        return f">= {usd.get('min')}"
    return f"{usd.get('min')} - {usd.get('max')}"


def diff_workpaper_snapshots(old_snapshot, new_snapshot):
    """Compare two build_workpaper_snapshot() outputs and return a flat list
    of {section, field, old, new} dicts describing what changed. Rows are
    matched by level_label (role name) on a best-effort basis since a
    workpaper's row/band structure is dynamic, not a fixed set of columns."""
    changes = []

    if (old_snapshot.get("buyer_name") or None) != (new_snapshot.get("buyer_name") or None):
        changes.append({
            "section": "Buyer", "field": "Name",
            "old": old_snapshot.get("buyer_name"), "new": new_snapshot.get("buyer_name"),
        })
    if (old_snapshot.get("creator_name") or None) != (new_snapshot.get("creator_name") or None):
        changes.append({
            "section": "PR Creator", "field": "Name",
            "old": old_snapshot.get("creator_name"), "new": new_snapshot.get("creator_name"),
        })

    old_rows = {r["level_label"]: r for r in old_snapshot.get("authority_rows", [])}
    new_rows = {r["level_label"]: r for r in new_snapshot.get("authority_rows", [])}
    new_bands = new_snapshot.get("bands", [])
    old_bands = old_snapshot.get("bands", [])

    for label in old_rows.keys() - new_rows.keys():
        old_r = old_rows[label]
        active_bands = [
            _band_short_label(old_bands[i])
            for i, chk in enumerate(old_r.get("checks", [])) if chk and i < len(old_bands)
        ]
        old_val_str = "Row existed"
        if active_bands:
            old_val_str += f" (authority: {', '.join(active_bands)})"
        changes.append({"section": "Approval Matrix", "field": label, "old": old_val_str, "new": "Row deleted"})

    for label in new_rows.keys() - old_rows.keys():
        new_r = new_rows[label]
        active_bands = [
            _band_short_label(new_bands[i])
            for i, chk in enumerate(new_r.get("checks", [])) if chk and i < len(new_bands)
        ]
        new_val_str = "Row added"
        if active_bands:
            new_val_str += f" (authority: {', '.join(active_bands)})"
        changes.append({"section": "Approval Matrix", "field": label, "old": "Row did not exist", "new": new_val_str})

    for label in old_rows.keys() & new_rows.keys():
        old_r, new_r = old_rows[label], new_rows[label]
        if (old_r.get("position") or None) != (new_r.get("position") or None):
            changes.append({
                "section": "Approval Matrix", "field": f"{label} - Position",
                "old": old_r.get("position"), "new": new_r.get("position"),
            })
        if (old_r.get("comment") or None) != (new_r.get("comment") or None):
            changes.append({
                "section": "Approval Matrix", "field": f"{label} - Comment",
                "old": old_r.get("comment"), "new": new_r.get("comment"),
            })
        old_checks = old_r.get("checks", [])
        new_checks = new_r.get("checks", [])
        for i in range(max(len(old_checks), len(new_checks))):
            ov = old_checks[i] if i < len(old_checks) else None
            nv = new_checks[i] if i < len(new_checks) else None
            if ov != nv:
                band = new_bands[i] if i < len(new_bands) else (old_bands[i] if i < len(old_bands) else None)
                changes.append({
                    "section": "Approval Matrix", "field": f"{label} - {_band_short_label(band)}",
                    "old": "Y" if ov else "N", "new": "Y" if nv else "N",
                })

    old_ses = old_snapshot.get("ses", [])
    new_ses = new_snapshot.get("ses", [])
    for i in range(max(len(old_ses), len(new_ses))):
        old_s = old_ses[i] if i < len(old_ses) else {}
        new_s = new_ses[i] if i < len(new_ses) else {}
        band = (new_s or {}).get("band_label") or (old_s or {}).get("band_label")
        band_desc = _band_short_label(band)
        if (old_s.get("creator") or None) != (new_s.get("creator") or None):
            changes.append({
                "section": "SES", "field": f"{band_desc} - Creator",
                "old": old_s.get("creator"), "new": new_s.get("creator"),
            })
        if (old_s.get("approver") or None) != (new_s.get("approver") or None):
            changes.append({
                "section": "SES", "field": f"{band_desc} - Approver",
                "old": old_s.get("approver"), "new": new_s.get("approver"),
            })

    return changes


def _process_workpaper_form(cur, request_form, unit_id):
    # Clear old rows and entries
    cur.execute("DELETE FROM workpaper_rows WHERE work_unit_id = %s", (unit_id,))
    cur.execute("DELETE FROM ses_entries WHERE work_unit_id = %s", (unit_id,))
    
    # Insert authority rows
    role_ids = request_form.getlist("row_role_id[]")
    positions = request_form.getlist("row_position[]")
    comments = request_form.getlist("row_comment[]")
    
    # We also need band_ids to check checkboxes
    cur.execute("""
        SELECT id FROM amount_tiers a
        WHERE EXISTS (
            SELECT 1 FROM tier_entities 
            WHERE tier_id = a.id AND entity_id = (SELECT entity_id FROM work_units WHERE id = %s)
        )
        AND (
            (SELECT site_id FROM work_units WHERE id = %s) IS NULL
            OR EXISTS (
                SELECT 1 FROM tier_sites 
                WHERE tier_id = a.id AND site_id = (SELECT site_id FROM work_units WHERE id = %s)
            )
        )
        ORDER BY seq
    """, (unit_id, unit_id, unit_id))
    bands = cur.fetchall()
    
    for i, role_id in enumerate(role_ids):
        if not role_id: continue
        position = positions[i] if i < len(positions) else ""
        comment = comments[i] if i < len(comments) else ""
        cur.execute(
            "INSERT INTO workpaper_rows (work_unit_id, row_kind, seq, role_id, position, comment) VALUES (%s, 'authority', %s, %s, %s, %s) RETURNING id",
            (unit_id, i+1, role_id, position, comment)
        )
        row_id = cur.fetchone()["id"]
        
        for b in bands:
            # checkbox name format: row_check_0_5
            if request_form.get(f"row_check_{i}_{b['id']}"):
                cur.execute(
                    "INSERT INTO workpaper_authority (row_id, tier_id, required) VALUES (%s, %s, TRUE)",
                    (row_id, b["id"])
                )

    # Insert buyer & creator
    buyer_role = request_form.get("buyer_role_id")
    creator_role = request_form.get("creator_role_id")
    
    if buyer_role:
        cur.execute("INSERT INTO workpaper_rows (work_unit_id, row_kind, seq, role_id) VALUES (%s, 'buyer', 998, %s)", (unit_id, buyer_role))
    if creator_role:
        cur.execute("INSERT INTO workpaper_rows (work_unit_id, row_kind, seq, role_id) VALUES (%s, 'creator', 999, %s)", (unit_id, creator_role))

    # Insert SES entries
    for b in bands:
        ses_creator = request_form.get(f"ses_creator_{b['id']}")
        ses_approver = request_form.get(f"ses_approver_{b['id']}")
        if ses_creator or ses_approver:
            # allow empty values to be inserted as NULL
            c_val = ses_creator if ses_creator else None
            a_val = ses_approver if ses_approver else None
            cur.execute(
                "INSERT INTO ses_entries (work_unit_id, tier_id, creator_role_id, approver_role_id) VALUES (%s, %s, %s, %s)",
                (unit_id, b["id"], c_val, a_val)
            )

@app.route("/workpapers/new", methods=["GET", "POST"])
def new_workpaper():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            entities, sites, roles = get_lookup_data(cur)
            # For new workpapers, we fetch all tiers initially
            cur.execute("SELECT id, seq, min_usd, max_usd FROM amount_tiers ORDER BY seq")
            # Create dummy unit with default IDR for label formatting
            dummy_idr = 16000
            bands = [{"id": t["id"], "label": format_tier_label(t["min_usd"], t["max_usd"], dummy_idr)} for t in cur.fetchall()]

            cur.execute("SELECT tier_id, entity_id FROM tier_entities")
            tier_entities_map = {}
            for row in cur.fetchall():
                t = row["tier_id"]
                tier_entities_map.setdefault(t, []).append(row["entity_id"])
                
            cur.execute("SELECT tier_id, site_id FROM tier_sites")
            tier_sites_map = {}
            for row in cur.fetchall():
                t = row["tier_id"]
                tier_sites_map.setdefault(t, []).append(row["site_id"])
            
            tier_mappings = {"entities": tier_entities_map, "sites": tier_sites_map}


            cur.execute("""
                SELECT ua.role_id, ua.entity_id, ua.site_id, ua.full_name, e.code as entity_code, s.name as site_name
                FROM user_assignments ua
                JOIN entities e ON ua.entity_id = e.id
                LEFT JOIN sites s ON ua.site_id = s.id
            """)
            assignments = cur.fetchall()
            role_assignments = {}
            for a in assignments:
                r = a["role_id"]
                if r not in role_assignments:
                    role_assignments[r] = []
                role_assignments[r].append({
                    "full_name": a["full_name"],
                    "entity_code": a["entity_code"],
                    "site_name": a["site_name"],
                    "entity_id": a["entity_id"],
                    "site_id": a["site_id"]
                })

            if request.method == "POST":
                entity_id = request.form.get("entity_id")
                site_id = request.form.get("site_id") or None
                code = request.form.get("code")
                project_name = request.form.get("project_name")
                status = request.form.get("status", "draft")
                
                try:
                    cur.execute(
                        """INSERT INTO work_units (entity_id, site_id, code, project_name, status,
                           version_major, version_minor, version_patch)
                           VALUES (%s, %s, %s, %s, %s, 1, 0, 0) RETURNING id""",
                        (entity_id, site_id, code, project_name, status)
                    )
                    unit_id = cur.fetchone()["id"]

                    _process_workpaper_form(cur, request.form, unit_id)

                    entity_code = next((e["code"] for e in entities if str(e["id"]) == str(entity_id)), None)
                    log_change(
                        conn, session["full_name"], "CREATE", entity_code=entity_code,
                        field_changed=None, old_value=None,
                        new_value=f"{code} - {project_name}",
                    )

                    conn.commit()
                    flash("Workpaper created successfully.", "success")
                    return redirect(url_for("workpaper_detail", code=code))
                except Exception as e:
                    conn.rollback()
                    flash(f"Error: {e}", "error")
                    
    finally:
        conn.close()
    
    return render_template("workpaper_form.html", mode="create", unit=None, entities=entities, sites=sites, roles=roles, bands=bands, authority_rows=[], matrix={}, buyer_row=None, creator_row=None, ses={}, role_assignments=role_assignments, tier_mappings=tier_mappings)

@app.route("/workpapers/<code>/edit", methods=["GET", "POST"])
def edit_workpaper(code):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM work_units WHERE code = %s", (code,))
            unit = cur.fetchone()
            if not unit:
                return redirect(url_for("workpapers"))

            # Auto-change status to editing when edit button is clicked (GET)
            if request.method == "GET" and unit.get("status") in ("draft", "published"):
                cur.execute("UPDATE work_units SET status = 'editing' WHERE id = %s", (unit["id"],))
                conn.commit()
                unit["status"] = 'editing'

            entities, sites, roles = get_lookup_data(cur)
            
            # For formatting bands
            cur.execute("SELECT exchange_rate_idr FROM entities WHERE id = %s", (unit["entity_id"],))
            idr = cur.fetchone()["exchange_rate_idr"]
            
            cur.execute("SELECT id, seq, min_usd, max_usd FROM amount_tiers ORDER BY seq")
            bands = [{"id": t["id"], "label": format_tier_label(t["min_usd"], t["max_usd"], idr)} for t in cur.fetchall()]

            cur.execute("SELECT tier_id, entity_id FROM tier_entities")
            tier_entities_map = {}
            for row in cur.fetchall():
                t = row["tier_id"]
                tier_entities_map.setdefault(t, []).append(row["entity_id"])
                
            cur.execute("SELECT tier_id, site_id FROM tier_sites")
            tier_sites_map = {}
            for row in cur.fetchall():
                t = row["tier_id"]
                tier_sites_map.setdefault(t, []).append(row["site_id"])
            
            tier_mappings = {"entities": tier_entities_map, "sites": tier_sites_map}

            if request.method == "POST":
                entity_id = request.form.get("entity_id")
                site_id = request.form.get("site_id") or None
                new_code = request.form.get("code")
                project_name = request.form.get("project_name")
                status = request.form.get("status", unit.get("status", "draft"))
                bump_type = request.form.get("bump_type") or "none"
                bump_comment = request.form.get("bump_comment", "").strip()

                try:
                    new_entity_code = next((e["code"] for e in entities if str(e["id"]) == str(entity_id)), None)
                    new_site_name = next(
                        (s["name"] for s in sites if str(s["id"]) == str(site_id)), None
                    ) if site_id else None
                    old_entity_code = next((e["code"] for e in entities if e["id"] == unit["entity_id"]), None)
                    old_site_name = next(
                        (s["name"] for s in sites if s["id"] == unit["site_id"]), None
                    ) if unit["site_id"] else None

                    diffs = []
                    if unit["code"] != new_code:
                        diffs.append(("code", unit["code"], new_code))
                    if unit["project_name"] != project_name:
                        diffs.append(("project_name", unit["project_name"], project_name))
                    if (unit.get("status") or "draft") != status:
                        diffs.append(("status", unit.get("status"), status))
                    if old_entity_code != new_entity_code:
                        diffs.append(("entity", old_entity_code, new_entity_code))
                    if (old_site_name or None) != (new_site_name or None):
                        diffs.append(("site", old_site_name, new_site_name))

                    old_snapshot = build_workpaper_snapshot(cur, unit["id"])

                    cur.execute(
                        "UPDATE work_units SET entity_id=%s, site_id=%s, code=%s, project_name=%s, status=%s WHERE id=%s",
                        (entity_id, site_id, new_code, project_name, status, unit["id"])
                    )
                    _process_workpaper_form(cur, request.form, unit["id"])

                    new_snapshot = build_workpaper_snapshot(cur, unit["id"])
                    content_diffs = diff_workpaper_snapshots(old_snapshot, new_snapshot)
                    all_changes = [
                        {"section": "Basic Info", "field": field, "old": old_value, "new": new_value}
                        for field, old_value, new_value in diffs
                    ] + content_diffs

                    if diffs:
                        for field, old_value, new_value in diffs:
                            log_change(
                                conn, session["full_name"], "UPDATE", entity_code=new_entity_code,
                                field_changed=field,
                                old_value=str(old_value) if old_value is not None else None,
                                new_value=str(new_value) if new_value is not None else None,
                            )
                    else:
                        log_change(
                            conn, session["full_name"], "UPDATE", entity_code=new_entity_code,
                            field_changed="Workpaper",
                            old_value=None,
                            new_value=(
                                f"{new_code} - {len(content_diffs)} change(s) to matrix/SES/buyer/creator"
                                if content_diffs else f"{new_code} - no content changes"
                            ),
                        )

                    # Detailed per-field diff always attaches to the workpaper's own
                    # version log (not the shared changelog table, which stays light).
                    is_real_bump = bump_type in ("major", "minor", "patch")
                    if is_real_bump:
                        maj, minn, pat, new_ver = _next_workpaper_version(unit, bump_type)
                        cur.execute(
                            "UPDATE work_units SET version_major=%s, version_minor=%s, version_patch=%s WHERE id=%s",
                            (maj, minn, pat, unit["id"])
                        )
                        # Assignment-driven changes queued since the last bump ride
                        # along with this bump; a plain save (no bump) leaves them queued.
                        all_changes = all_changes + consume_pending_changes(cur, unit["id"])
                    else:
                        new_ver = f"{unit['version_major']}.{unit['version_minor']}.{unit['version_patch']}"

                    if all_changes or is_real_bump:
                        cur.execute(
                            """INSERT INTO workpaper_version_log
                               (work_unit_id, version, bump_type, comment, author, snapshot, changes)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            (unit["id"], new_ver, bump_type, bump_comment or None,
                             session["full_name"], Json(new_snapshot), Json(all_changes))
                        )

                    conn.commit()
                    flash(
                        "Workpaper updated successfully." + (f" Version bumped to v{new_ver}." if is_real_bump else ""),
                        "success",
                    )
                    return redirect(url_for("workpaper_detail", code=new_code))
                except Exception as e:
                    conn.rollback()
                    flash(f"Error: {e}", "error")

            cur.execute("SELECT * FROM workpaper_rows WHERE work_unit_id = %s ORDER BY seq", (unit["id"],))
            rows = cur.fetchall()
            authority_rows = [r for r in rows if r["row_kind"] == "authority"]
            buyer_row = next((r for r in rows if r["row_kind"] == "buyer"), None)
            creator_row = next((r for r in rows if r["row_kind"] == "creator"), None)
            
            cur.execute(
                "SELECT wa.row_id, wa.tier_id, wa.required FROM workpaper_authority wa JOIN workpaper_rows wr ON wr.id = wa.row_id WHERE wr.work_unit_id = %s",
                (unit["id"],)
            )
            matrix = {(m["row_id"], m["tier_id"]): m["required"] for m in cur.fetchall()}
            
            cur.execute("SELECT * FROM ses_entries WHERE work_unit_id = %s", (unit["id"],))
            ses = {s["tier_id"]: s for s in cur.fetchall()}
            
            cur.execute("""
                SELECT ua.role_id, ua.entity_id, ua.site_id, ua.full_name, e.code as entity_code, s.name as site_name
                FROM user_assignments ua
                JOIN entities e ON ua.entity_id = e.id
                LEFT JOIN sites s ON ua.site_id = s.id
            """)
            assignments = cur.fetchall()
            role_assignments = {}
            for a in assignments:
                r = a["role_id"]
                if r not in role_assignments:
                    role_assignments[r] = []
                role_assignments[r].append({
                    "full_name": a["full_name"],
                    "entity_code": a["entity_code"],
                    "site_name": a["site_name"],
                    "entity_id": a["entity_id"],
                    "site_id": a["site_id"]
                })
            
    finally:
        conn.close()
    
    return render_template("workpaper_form.html", mode="edit", unit=unit, entities=entities, sites=sites, roles=roles, bands=bands, authority_rows=authority_rows, matrix=matrix, buyer_row=buyer_row, creator_row=creator_row, ses=ses, role_assignments=role_assignments, tier_mappings=tier_mappings)

@app.route("/workpapers/<int:unit_id>/delete", methods=["POST"])
def delete_workpaper(unit_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT wu.code, wu.project_name, e.code AS entity_code
                FROM work_units wu
                JOIN entities e ON e.id = wu.entity_id
                WHERE wu.id = %s
                """,
                (unit_id,),
            )
            existing = cur.fetchone()
            if not existing:
                flash("Workpaper not found.", "error")
                return redirect(url_for("workpapers"))

            cur.execute("DELETE FROM work_units WHERE id = %s", (unit_id,))
            log_change(
                conn, session["full_name"], "DELETE", entity_code=existing["entity_code"],
                field_changed=None,
                old_value=f"{existing['code']} - {existing['project_name']}",
                new_value=None,
            )
            conn.commit()
            flash("Workpaper deleted successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash("Failed to delete workpaper.", "error")
    finally:
        conn.close()
    return redirect(url_for("workpapers"))


@app.route("/workpapers/<int:unit_id>/version-bump", methods=["POST"])
def workpaper_version_bump(unit_id):
    bump_type = request.form.get("bump_type")   # 'major','minor','patch'
    comment   = request.form.get("comment", "").strip()
    if bump_type not in ("major", "minor", "patch"):
        flash("Invalid bump type.", "error")
        return redirect(url_for("workpapers"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT wu.*, e.code AS entity_code
                FROM work_units wu
                JOIN entities e ON e.id = wu.entity_id
                WHERE wu.id = %s
                """,
                (unit_id,),
            )
            unit = cur.fetchone()
            if not unit:
                flash("Workpaper not found.", "error")
                return redirect(url_for("workpapers"))

            old_ver = f"{unit['version_major']}.{unit['version_minor']}.{unit['version_patch']}"
            maj, minn, pat, new_ver = _next_workpaper_version(unit, bump_type)

            cur.execute(
                "UPDATE work_units SET version_major=%s, version_minor=%s, version_patch=%s WHERE id=%s",
                (maj, minn, pat, unit_id)
            )
            snapshot = build_workpaper_snapshot(cur, unit_id)
            pending_changes = consume_pending_changes(cur, unit_id)
            cur.execute(
                """INSERT INTO workpaper_version_log (work_unit_id, version, bump_type, comment, author, snapshot, changes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (unit_id, new_ver, bump_type, comment, session.get("full_name", "System"),
                 Json(snapshot), Json(pending_changes)),
            )
            log_change(
                conn, session.get("full_name", "System"), "UPDATE", entity_code=unit["entity_code"],
                field_changed=f"version ({bump_type})",
                old_value=old_ver,
                new_value=f"{new_ver}: {comment}" if comment else new_ver,
            )
            conn.commit()
            flash(f"Version bumped to {new_ver}.", "success")
            return redirect(url_for("workpaper_detail", code=unit["code"]))
    except Exception as e:
        conn.rollback()
        flash(f"Error: {e}", "error")
        return redirect(url_for("workpapers"))
    finally:
        conn.close()


@app.route("/workpapers/<code>/version/<int:log_id>")
def workpaper_version_snapshot(code, log_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT vl.id, vl.version, vl.bump_type, vl.comment, vl.author, vl.created_at,
                       vl.snapshot, vl.changes
                FROM workpaper_version_log vl
                JOIN work_units wu ON wu.id = vl.work_unit_id
                WHERE vl.id = %s AND wu.code = %s
                """,
                (log_id, code),
            )
            log = cur.fetchone()
    finally:
        conn.close()

    if not log or not log["snapshot"]:
        flash("Snapshot not available for this version (created before this feature existed).", "error")
        return redirect(url_for("workpaper_detail", code=code))

    return render_template("workpaper_version_snapshot.html", code=code, log=log, snapshot=log["snapshot"])


# ---------- Changelog ----------

@app.route("/changelog")
def changelog():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT admin_name, action, entity_code, field_changed, old_value, new_value, changed_at
                FROM changelog
                ORDER BY changed_at DESC
                LIMIT 200
                """
            )
            entries = cur.fetchall()
    finally:
        conn.close()

    return render_template("changelog.html", entries=entries)


# ---------- Export ----------

@app.route("/export")
def export():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            buffer = build_export_workbook(cur)
    finally:
        conn.close()

    filename = f"authority_matrix_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


# ---------- Entities CRUD ----------

@app.route("/entities")
def entities_list():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.*, 
                       array_to_string(array_agg(s.name ORDER BY s.name), ', ') as site_names 
                FROM entities e
                LEFT JOIN entity_sites es ON es.entity_id = e.id
                LEFT JOIN sites s ON s.id = es.site_id
                GROUP BY e.id
                ORDER BY e.code
            """)
            entities = cur.fetchall()
    finally:
        conn.close()
    return render_template("entities.html", entities=entities)

@app.route("/entities/new", methods=["GET", "POST"])
def new_entity():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM sites ORDER BY name")
            sites = cur.fetchall()
            if request.method == "POST":
                code = request.form.get("code", "").strip().upper()
                name = request.form.get("name", "").strip()
                exchange_rate_idr = request.form.get("exchange_rate_idr", 16000)
                site_ids = request.form.getlist("site_id")
                try:
                    cur.execute(
                        "INSERT INTO entities (code, name, exchange_rate_idr) VALUES (%s, %s, %s) RETURNING id",
                        (code, name, exchange_rate_idr)
                    )
                    new_entity_id = cur.fetchone()["id"]
                    
                    for s_id in site_ids:
                        cur.execute("INSERT INTO entity_sites (entity_id, site_id) VALUES (%s, %s)", (new_entity_id, s_id))
                        
                    log_change(conn, session["full_name"], "CREATE", entity_code=code, field_changed="Entity", old_value=None, new_value=name)
                    conn.commit()
                    flash("Entity created successfully.", "success")
                    return redirect(url_for("entities_list"))
                except Exception as e:
                    conn.rollback()
                    flash("Error creating entity. Code might already exist.", "error")
    finally:
        conn.close()
    return render_template("entity_form.html", mode="create", entity=None, sites=sites)

@app.route("/entities/<int:entity_id>/edit", methods=["GET", "POST"])
def edit_entity(entity_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM entities WHERE id = %s", (entity_id,))
            entity = cur.fetchone()
            if not entity:
                flash("Entity not found.", "error")
                return redirect(url_for("entities_list"))
            cur.execute("SELECT site_id FROM entity_sites WHERE entity_id = %s", (entity_id,))
            entity_sites = [row["site_id"] for row in cur.fetchall()]
            
            cur.execute("SELECT id, name FROM sites ORDER BY name")
            sites = cur.fetchall()
            
            if request.method == "POST":
                code = request.form.get("code", "").strip().upper()
                name = request.form.get("name", "").strip()
                exchange_rate_idr = request.form.get("exchange_rate_idr", 16000)
                site_ids = request.form.getlist("site_id")
                try:
                    cur.execute(
                        "UPDATE entities SET code=%s, name=%s, exchange_rate_idr=%s WHERE id=%s",
                        (code, name, exchange_rate_idr, entity_id)
                    )
                    cur.execute("DELETE FROM entity_sites WHERE entity_id=%s", (entity_id,))
                    for s_id in site_ids:
                        cur.execute("INSERT INTO entity_sites (entity_id, site_id) VALUES (%s, %s)", (entity_id, s_id))
                        
                    log_change(conn, session["full_name"], "UPDATE", entity_code=code, field_changed="Entity details", old_value=entity["name"], new_value=name)
                    conn.commit()
                    flash("Entity updated.", "success")
                    return redirect(url_for("entities_list"))
                except Exception as e:
                    conn.rollback()
                    flash("Error updating entity.", "error")
    finally:
        conn.close()
    return render_template("entity_form.html", mode="edit", entity=entity, sites=sites, entity_sites=entity_sites)

@app.route("/entities/<int:entity_id>/delete", methods=["POST"])
def delete_entity(entity_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM entities WHERE id = %s", (entity_id,))
            entity = cur.fetchone()
            if entity:
                cur.execute("DELETE FROM entities WHERE id = %s", (entity_id,))
                log_change(conn, session["full_name"], "DELETE", entity_code=entity["code"], field_changed="Entity", old_value=entity["name"], new_value=None)
                conn.commit()
                flash("Entity deleted.", "success")
    except Exception as e:
        conn.rollback()
        flash("Cannot delete entity. It might be used in other records.", "error")
    finally:
        conn.close()
    return redirect(url_for("entities_list"))


# ---------- Sites CRUD ----------

@app.route("/sites")
def sites_list():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.id, s.name, 
                       array_to_string(array_agg(e.code ORDER BY e.code), ', ') as entity_code 
                FROM sites s 
                LEFT JOIN entity_sites es ON es.site_id = s.id
                LEFT JOIN entities e ON e.id = es.entity_id 
                GROUP BY s.id, s.name
                ORDER BY s.name
            """)
            sites = cur.fetchall()
    finally:
        conn.close()
    return render_template("sites.html", sites=sites)

@app.route("/sites/new", methods=["GET", "POST"])
def new_site():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, code, name FROM entities ORDER BY code")
            entities = cur.fetchall()
            if request.method == "POST":
                entity_ids = request.form.getlist("entity_id")
                name = request.form.get("name", "").strip()
                try:
                    cur.execute("INSERT INTO sites (name) VALUES (%s) RETURNING id", (name,))
                    new_site_id = cur.fetchone()["id"]
                    for e_id in entity_ids:
                        cur.execute("INSERT INTO entity_sites (entity_id, site_id) VALUES (%s, %s)", (e_id, new_site_id))
                    
                    e_codes = [e["code"] for e in entities if str(e["id"]) in entity_ids]
                    log_change(conn, session["full_name"], "CREATE", entity_code=", ".join(e_codes), field_changed="Site", old_value=None, new_value=name)
                    conn.commit()
                    flash("Site created.", "success")
                    return redirect(url_for("sites_list"))
                except Exception as e:
                    conn.rollback()
                    flash("Error creating site.", "error")
    finally:
        conn.close()
    return render_template("site_form.html", mode="create", site=None, entities=entities)

@app.route("/sites/<int:site_id>/edit", methods=["GET", "POST"])
def edit_site(site_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sites WHERE id = %s", (site_id,))
            site = cur.fetchone()
            if not site:
                flash("Site not found.", "error")
                return redirect(url_for("sites_list"))
            
            cur.execute("SELECT entity_id FROM entity_sites WHERE site_id = %s", (site_id,))
            site_entities = [row["entity_id"] for row in cur.fetchall()]
            
            cur.execute("SELECT id, code, name FROM entities ORDER BY code")
            entities = cur.fetchall()
            
            if request.method == "POST":
                entity_ids = request.form.getlist("entity_id")
                name = request.form.get("name", "").strip()
                try:
                    cur.execute("UPDATE sites SET name=%s WHERE id=%s", (name, site_id))
                    cur.execute("DELETE FROM entity_sites WHERE site_id=%s", (site_id,))
                    for e_id in entity_ids:
                        cur.execute("INSERT INTO entity_sites (entity_id, site_id) VALUES (%s, %s)", (e_id, site_id))
                    
                    e_codes = [e["code"] for e in entities if str(e["id"]) in entity_ids]
                    log_change(conn, session["full_name"], "UPDATE", entity_code=", ".join(e_codes), field_changed="Site", old_value=site["name"], new_value=name)
                    conn.commit()
                    flash("Site updated.", "success")
                    return redirect(url_for("sites_list"))
                except Exception as e:
                    conn.rollback()
                    flash("Error updating site.", "error")
    finally:
        conn.close()
    return render_template("site_form.html", mode="edit", site=site, entities=entities, site_entities=site_entities)

@app.route("/sites/<int:site_id>/delete", methods=["POST"])
def delete_site(site_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    conn = get_db_connection()
    site_name = None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.*, array_to_string(array_agg(e.code), ', ') as e_code
                FROM sites s
                LEFT JOIN entity_sites es ON es.site_id = s.id
                LEFT JOIN entities e ON e.id = es.entity_id
                WHERE s.id = %s
                GROUP BY s.id
            """, (site_id,))
            site = cur.fetchone()
            if site:
                site_name = site["name"]
                cur.execute("DELETE FROM sites WHERE id = %s", (site_id,))
                log_change(conn, session["full_name"], "DELETE", entity_code=site["e_code"], field_changed="Site", old_value=site["name"], new_value=None)
                conn.commit()
                if is_ajax:
                    return jsonify(success=True, message=f"“{site_name}” deleted.")
                flash("Site deleted.", "success")
            elif is_ajax:
                return jsonify(success=False, message="Site not found."), 404
    except Exception as e:
        conn.rollback()
        label = f"“{site_name}”" if site_name else "Site"
        message = f"Cannot delete {label}. It might be used in other records."
        if is_ajax:
            return jsonify(success=False, message=message), 400
        flash(message, "error")
    finally:
        conn.close()
    return redirect(url_for("sites_list"))


# ---------- Employees CRUD (Bulk Edit over user_assignments) ----------

@app.route("/employees/edit", methods=["GET", "POST"])
def edit_employee():
    old_email = request.args.get("email")
    if not old_email:
        return redirect(url_for("employees"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT full_name, email FROM user_assignments WHERE email = %s LIMIT 1", (old_email,))
            emp = cur.fetchone()
            if not emp:
                flash("Employee not found.", "error")
                return redirect(url_for("employees"))
            
            if request.method == "POST":
                new_name = request.form.get("full_name", "").strip()
                new_email = request.form.get("email", "").strip()
                
                if new_name and new_email:
                    try:
                        cur.execute("UPDATE user_assignments SET full_name=%s, email=%s WHERE email=%s", (new_name, new_email, old_email))
                        log_change(conn, session["full_name"], "UPDATE", entity_code="ALL", field_changed="Employee", old_value=old_email, new_value=new_email)
                        conn.commit()
                        flash(f"Updated records for {new_name}.", "success")
                        return redirect(url_for("employees"))
                    except Exception as e:
                        conn.rollback()
                        flash("Error updating employee records. Email might conflict with another existing user.", "error")

            # Fetch all assignments for display
            cur.execute("""
                SELECT e.code as entity_code, s.name as site_name, r.name as role_name, ua.is_active
                FROM user_assignments ua
                JOIN entities e ON e.id = ua.entity_id
                JOIN roles r ON r.id = ua.role_id
                LEFT JOIN sites s ON s.id = ua.site_id
                WHERE ua.email = %s
                ORDER BY e.code, s.name, r.name
            """, (old_email,))
            assignments = cur.fetchall()
    finally:
        conn.close()

    return render_template("employee_form.html", employee=emp, assignments=assignments)

@app.route("/employees/delete", methods=["POST"])
def delete_employee():
    email = request.args.get("email")
    if email:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_assignments WHERE email = %s", (email,))
                log_change(conn, session["full_name"], "DELETE", entity_code="ALL", field_changed="Employee", old_value=email, new_value=None)
                conn.commit()
                flash(f"Deleted all assignments for {email}.", "success")
        except Exception as e:
            conn.rollback()
            flash("Error deleting employee.", "error")
        finally:
            conn.close()
    return redirect(url_for("employees"))

if __name__ == "__main__":
    app.run(debug=True)
