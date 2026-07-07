import os
import sys
from datetime import datetime

import psycopg2
from flask import Flask, flash, redirect, render_template, request, send_file, url_for

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import get_db_connection, log_change  # noqa: E402
from lib.export import build_export_workbook  # noqa: E402

app = Flask(__name__, template_folder="../templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev")

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
    admin_name = request.form.get("admin_name", "").strip()
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    entity_id = request.form.get("entity_id") or ""
    role_id = request.form.get("role_id") or ""
    site_id = request.form.get("site_id") or ""
    is_active = "is_active" in request.form

    errors = []
    if not admin_name:
        errors.append("Changed by is required.")
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
        "admin_name": admin_name,
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
        "admin_name": data["admin_name"],
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
                        conn, data["admin_name"], "CREATE", entity_code=entity_code,
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
                            conn, data["admin_name"], "UPDATE", entity_code=new_entity_code,
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
    admin_name = request.form.get("admin_name", "").strip() or "Unknown"

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
                conn, admin_name, "DELETE", entity_code=existing["entity_code"],
                field_changed=None,
                old_value=f"{existing['full_name']} - {existing['role_name']}",
                new_value=None,
            )
            conn.commit()
    finally:
        conn.close()

    flash("Assignment deleted.", "success")
    return redirect(url_for("assignments"))


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
