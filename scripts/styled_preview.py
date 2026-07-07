"""Self-contained styled preview of the kertas-kerja detail view (no external CSS).

Mirrors templates/workpaper_detail.html visually so it can be screenshotted or
shared offline. The live app renders the same layout via Tailwind.
"""
import html
import json
import sys

data = json.load(open(sys.argv[1]))["units"]
code = sys.argv[2] if len(sys.argv) > 2 else "IMN-SBY"
out_path = sys.argv[3]
p = data[code]


def esc(s):
    return html.escape(s or "")


band_ths = ""
for b in p["bands"]:
    parts = b.split(" or ")
    sub = f"<div class='sub'>{esc(parts[1])}</div>" if len(parts) > 1 else ""
    band_ths += f"<th class='band'><div>{esc(parts[0])}</div>{sub}</th>"

auth_rows = ""
for r in p["rows"]:
    if r["kind"] != "authority":
        continue
    cells = ""
    for i in range(len(p["bands"])):
        req = r["bands"].get(str(i), r["bands"].get(i))
        cells += (f"<td class='yes'>✓</td>" if req else "<td class='no'>·</td>")
    comment = f"<div class='cmt'>{esc(r['comment'])}</div>" if r["comment"] else ""
    auth_rows += (
        f"<tr><td class='lvl'>{esc(r['level'])}</td>"
        f"<td>{esc(r['name'])}{comment}</td>"
        f"<td class='pos'>{esc(r['position'])}</td>{cells}</tr>"
    )

buyer = next((r["name"] for r in p["rows"] if r["kind"] == "buyer"), "—")
creator = next((r["name"] for r in p["rows"] if r["kind"] == "creator"), "—")

ses_rows = ""
for i, b in enumerate(p["bands"]):
    s = p["ses"][i] if i < len(p["ses"]) else {"creator": "—", "approver": "—"}
    ses_rows += (f"<tr><td>{esc(b)}</td><td>{esc(s['creator'])}</td>"
                 f"<td>{esc(s['approver'])}</td></tr>")

doc = f"""<!doctype html><html><head><meta charset='utf-8'><style>
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
        color:#111827; background:#f3f4f6; margin:0; padding:24px; }}
.sheet {{ max-width:1120px; margin:0 auto; background:#fff; border:1px solid #d1d5db;
          border-radius:10px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
.head {{ display:grid; grid-template-columns:1fr 1fr; border-bottom:1px solid #d1d5db; }}
.head > div {{ padding:10px 16px; }}
.head > div:first-child {{ border-right:1px solid #d1d5db; }}
.lbl {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:#6b7280; }}
.val {{ font-weight:600; }}
.section {{ padding:12px 16px 4px; font-size:11px; font-weight:700; text-transform:uppercase;
           letter-spacing:.05em; color:#374151; }}
.section.brdr {{ border-top:1px solid #d1d5db; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th, td {{ border:1px solid #d1d5db; padding:5px 8px; }}
thead th {{ background:#f3f4f6; color:#374151; text-align:left; vertical-align:bottom; }}
th.band {{ text-align:center; width:80px; font-size:11px; }}
th.band .sub {{ font-weight:400; color:#9ca3af; font-size:10px; }}
td.lvl {{ font-weight:600; width:150px; }}
td.pos {{ color:#4b5563; width:210px; }}
td.yes {{ text-align:center; background:#ecfdf5; color:#047857; font-weight:700; }}
td.no {{ text-align:center; color:#d1d5db; }}
.cmt {{ font-size:10px; color:#d97706; margin-top:2px; }}
.pair {{ display:grid; grid-template-columns:1fr 1fr; gap:1px; background:#d1d5db;
         border-top:1px solid #d1d5db; }}
.pair > div {{ background:#fff; padding:8px 16px; font-size:12px; }}
.wrap {{ overflow-x:auto; }}
</style></head><body>
<div class='sheet'>
  <div class='head'>
    <div><div class='lbl'>Nama Entitas / Company Name</div><div class='val'>{esc(p['entity_name'])}</div></div>
    <div><div class='lbl'>Proyek / Project</div><div class='val'>{esc(p['project_name'])}</div></div>
  </div>
  <div class='section'>Purchase Request per event</div>
  <div class='wrap'><table>
    <thead><tr><th style='width:150px'>Level / Tingkat</th><th>Name / Nama</th>
      <th style='width:210px'>Position</th>{band_ths}</tr></thead>
    <tbody>{auth_rows}</tbody>
  </table></div>
  <div class='pair'>
    <div><div class='lbl'>Buyer</div>{esc(buyer)}</div>
    <div><div class='lbl'>Purchase Request Creator</div>{esc(creator)}</div>
  </div>
  <div class='section brdr'>Service Entry Sheet (SES)</div>
  <div class='wrap'><table>
    <thead><tr><th>Nominal / Threshold</th><th style='width:33%'>SES Creator</th>
      <th style='width:33%'>SES Approver</th></tr></thead>
    <tbody>{ses_rows}</tbody>
  </table></div>
</div></body></html>"""

open(out_path, "w").write(doc)
print("styled preview ->", out_path)
