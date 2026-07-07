"""Render workpaper templates offline (no DB) to verify them and produce a preview.

Uses the real Flask app's Jinja environment + url_for, feeding it mock data
shaped exactly like the DB query results, sourced from the parsed workbook.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://x/x")  # never connected

from api.index import app  # noqa: E402
from flask import render_template  # noqa: E402

BADGE = {"IMU": "blue", "ILSS": "teal", "IMN": "indigo", "IRB": "orange",
         "PGE": "green", "MBN": "purple", "ISB": "yellow", "INDIS": "gray", "KGTE": "pink"}

data = json.load(open(sys.argv[1]))["units"]
code = sys.argv[2] if len(sys.argv) > 2 else "IMN-SBY"
p = data[code]
ecode = code.split("-")[0].split(" ")[0]

bands = [{"id": i, "seq": i, "label": lbl} for i, lbl in enumerate(p["bands"])]
rows, matrix = [], {}
rid = 0
for r in p["rows"]:
    rid += 1
    rows.append({"id": rid, "row_kind": r["kind"], "seq": rid,
                 "level_label": r["level"], "person_name": r["name"],
                 "position": r["position"], "comment": r["comment"]})
    if r["kind"] == "authority":
        for bseq, req in r["bands"].items():
            matrix[(rid, int(bseq))] = req
ses = {i: {"creator_name": s["creator"], "approver_name": s["approver"]}
       for i, s in enumerate(p["ses"])}

unit = {"id": 1, "code": code, "project_name": p["project_name"],
        "entity_id": 1, "entity_code": ecode, "entity_name": p["entity_name"]}
all_units = [{"code": k, "project_name": v["project_name"]} for k, v in data.items()]
authority_rows = [r for r in rows if r["row_kind"] == "authority"]
buyer_row = next((r for r in rows if r["row_kind"] == "buyer"), None)
creator_row = next((r for r in rows if r["row_kind"] == "creator"), None)

groups = {}
for k, v in data.items():
    ec = k.split("-")[0].split(" ")[0]
    groups.setdefault((ec, v["entity_name"]), []).append({"code": k, "project_name": v["project_name"]})
group_list = [{"entity_code": a, "entity_name": b, "units": u} for (a, b), u in groups.items()]

with app.test_request_context("/"):
    detail = render_template(
        "workpaper_detail.html", unit=unit, bands=bands, authority_rows=authority_rows,
        buyer_row=buyer_row, creator_row=creator_row, matrix=matrix, ses=ses, all_units=all_units)
    listing = render_template("workpapers.html", groups=group_list, total_units=len(all_units))

out_dir = sys.argv[3] if len(sys.argv) > 3 else "/tmp"
open(os.path.join(out_dir, "preview_detail.html"), "w").write(detail)
open(os.path.join(out_dir, "preview_list.html"), "w").write(listing)
print("OK rendered", code, "->", out_dir)
