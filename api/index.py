import os
import sys
from datetime import datetime

import psycopg2
from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.auth import get_user_by_email, set_password, verify_password  # noqa: E402
from lib.db import get_db_connection, log_change  # noqa: E402
from lib.export import build_export_workbook  # noqa: E402
from lib.money import format_tier_label  # noqa: E402

app = Flask(__name__, template_folder="../templates", static_folder="../static")
app.secret_key = os.environ.get("SECRET_KEY", "dev")

PUBLIC_ENDPOINTS = {"login", "static"}


def _safe_next(path):
    """Only allow same-site relative redirects (blocks open-redirect via ?next=)."""
    if path and path.startswith("/") and not path.startswith("//"):
        return path
    return None


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if not session.get("user_id"):
        return redirect(url_for("login", next=request.path))
    if session.get("must_change_password") and request.endpoint != "change_password":
        return redirect(url_for("change_password"))
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
            flash("Email atau password salah.", "error")
            return render_template("login.html", email=email, next=next_path)

        session.clear()
        session["user_id"] = user["id"]
        session["full_name"] = user["full_name"]
        session["email"] = user["email"]
        session["must_change_password"] = user["must_change_password"]
        return redirect(_safe_next(next_path) or url_for("home"))

    return render_template("login.html", email="", next=request.args.get("next", ""))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Anda telah logout.", "success")
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
                flash("Password saat ini salah.", "error")
            elif len(new_password) < 8:
                flash("Password baru minimal 8 karakter.", "error")
            elif new_password != confirm_password:
                flash("Konfirmasi password tidak cocok.", "error")
            else:
                set_password(conn, user["id"], new_password)
                conn.commit()
                session["must_change_password"] = False
                flash("Password berhasil diubah.", "success")
                return redirect(url_for("home"))
        finally:
            conn.close()

    return render_template("change_password.html", forced=session.get("must_change_password", False))

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


app.jinja_env.globals["entity_badge_class"] = entity_badge_class


def get_lookup_data(cur):
    cur.execute("SELECT id, code, name FROM entities ORDER BY code")
    entities = cur.fetchall()

    cur.execute(
        """
        SELECT s.id, s.name, e.code AS entity_code
        FROM sites s
        JOIN entities e ON e.id = s.entity_id
        ORDER BY e.code, s.name
        """
    )
    sites = cur.fetchall()

    cur.execute("SELECT id, name, is_centralized FROM roles ORDER BY name")
    roles = cur.fetchall()

    return entities, sites, roles


def get_ho_jakarta_site_id(cur):
    cur.execute("SELECT id FROM sites WHERE name = %s LIMIT 1", ("HO Jakarta",))
    row = cur.fetchone()
    return row["id"] if row else None


@app.route("/")
def home():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
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

            entities, _sites, roles = get_lookup_data(cur)
    finally:
        conn.close()

    return render_template(
        "assignments.html",
        assignments=rows,
        entities=entities,
        roles=roles,
        selected_entity=entity_code,
        selected_role=role_id,
    )


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

            if request.method == "POST":
                data, errors = _read_assignment_form(cur, roles)

                if errors:
                    for message in errors:
                        flash(message, "error")
                    return render_template(
                        "assignment_form.html", mode="create", assignment=_sticky_assignment(data),
                        entities=entities, sites=sites, roles=roles,
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
                    conn.commit()
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    flash("An assignment with this email, entity, and role already exists.", "error")
                    return render_template(
                        "assignment_form.html", mode="create", assignment=_sticky_assignment(data),
                        entities=entities, sites=sites, roles=roles,
                    )

                flash("Assignment added.", "success")
                return redirect(url_for("assignments"))

            assignment = None
    finally:
        conn.close()

    return render_template(
        "assignment_form.html", mode="create", assignment=assignment,
        entities=entities, sites=sites, roles=roles,
    )


@app.route("/assignments/<int:assignment_id>/edit", methods=["GET", "POST"])
def edit_assignment(assignment_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            entities, sites, roles = get_lookup_data(cur)

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
                        entities=entities, sites=sites, roles=roles,
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
                    conn.commit()
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    flash("An assignment with this email, entity, and role already exists.", "error")
                    return render_template(
                        "assignment_form.html", mode="edit",
                        assignment=_sticky_assignment(data, {"id": assignment_id}),
                        entities=entities, sites=sites, roles=roles,
                    )

                flash("Assignment updated.", "success")
                return redirect(url_for("assignments"))

            assignment = dict(existing)
    finally:
        conn.close()

    return render_template(
        "assignment_form.html", mode="edit", assignment=assignment,
        entities=entities, sites=sites, roles=roles,
    )


@app.route("/assignments/<int:assignment_id>/delete", methods=["POST"])
def delete_assignment(assignment_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ua.full_name, e.code AS entity_code, r.name AS role_name
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
            conn.commit()
    finally:
        conn.close()

    flash("Assignment deleted.", "success")
    return redirect(url_for("assignments"))


# ---------- Kertas Kerja (Workpapers) ----------

@app.route("/workpapers")
def workpapers():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT wu.id, wu.code, wu.project_name,
                       e.code AS entity_code, e.name AS entity_name
                FROM work_units wu
                JOIN entities e ON e.id = wu.entity_id
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
                SELECT wu.id, wu.code, wu.project_name, wu.entity_id,
                       e.code AS entity_code, e.name AS entity_name, e.exchange_rate_idr
                FROM work_units wu
                JOIN entities e ON e.id = wu.entity_id
                WHERE wu.code = %s
                """,
                (code,),
            )
            unit = cur.fetchone()
            if not unit:
                flash("Kertas kerja tidak ditemukan.", "error")
                return redirect(url_for("workpapers"))

            cur.execute("SELECT id, seq, min_usd, max_usd FROM amount_tiers ORDER BY seq")
            bands = [
                {
                    "id": t["id"],
                    "seq": t["seq"],
                    "label": format_tier_label(t["min_usd"], t["max_usd"], unit["exchange_rate_idr"]),
                }
                for t in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT wr.id, wr.row_kind, wr.seq, r.name AS level_label,
                       wr.person_name, p.title AS position, wr.comment
                FROM workpaper_rows wr
                LEFT JOIN roles r ON r.id = wr.role_id
                LEFT JOIN positions p ON p.id = wr.position_id
                WHERE wr.work_unit_id = %s
                ORDER BY wr.seq
                """,
                (unit["id"],),
            )
            rows = cur.fetchall()

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
                SELECT tier_id, creator_name, approver_name
                FROM ses_entries
                WHERE work_unit_id = %s
                """,
                (unit["id"],),
            )
            ses = {s["tier_id"]: s for s in cur.fetchall()}

            cur.execute(
                "SELECT code, project_name FROM work_units ORDER BY sort_order, code"
            )
            all_units = cur.fetchall()
    finally:
        conn.close()

    authority_rows = [r for r in rows if r["row_kind"] == "authority"]
    buyer_row = next((r for r in rows if r["row_kind"] == "buyer"), None)
    creator_row = next((r for r in rows if r["row_kind"] == "creator"), None)

    return render_template(
        "workpaper_detail.html",
        unit=unit, bands=bands, authority_rows=authority_rows,
        buyer_row=buyer_row, creator_row=creator_row,
        matrix=matrix, ses=ses, all_units=all_units,
    )


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


if __name__ == "__main__":
    app.run(debug=True)
