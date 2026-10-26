"""Email a summary when a new month of Flintshire crime data lands. Sent via the Deeside server mailer."""
import json, subprocess, html, datetime
from pathlib import Path

NAMES = {"violent-crime": "Violence and sexual offences", "anti-social-behaviour": "Anti-social behaviour",
         "criminal-damage-arson": "Criminal damage and arson", "shoplifting": "Shoplifting", "public-order": "Public order",
         "other-theft": "Other theft", "vehicle-crime": "Vehicle crime", "other-crime": "Other crime", "burglary": "Burglary",
         "drugs": "Drugs", "possession-of-weapons": "Possession of weapons", "bicycle-theft": "Bicycle theft",
         "robbery": "Robbery", "theft-from-the-person": "Theft from the person"}
D = Path(__file__).parent / "data"
idx = json.loads((D / "index.json").read_text())
areas = {a["id"]: a["name"] for a in json.loads((D / "areas-lite.json").read_text())}
ms = idx["months"]
cur, prev = ms[0], ms[1]
yr = next((m for m in ms if m["month"] == str(int(cur["month"][:4]) - 1) + cur["month"][4:]), None)
avg = sum(m["total"] for m in ms[:12]) / min(12, len(ms))


def mname(m):
    return datetime.date(int(m[:4]), int(m[5:]), 1).strftime("%B %Y")


def pc(a, b):
    return f"{(a - b) / b * 100:+.1f}%" if b else "n/a"


rows = "".join(f"<tr><td>{NAMES.get(c, c)}</td><td align=right><b>{n}</b></td><td align=right>{prev['categories'].get(c, 0)}</td></tr>"
               for c, n in sorted(cur["categories"].items(), key=lambda x: -x[1]))
moves = sorted(((cur["by_area"].get(a, 0) - prev["by_area"].get(a, 0), a) for a in areas), reverse=True)
li = lambda a: f"<li>{html.escape(areas[a])}: {prev['by_area'].get(a, 0)} to {cur['by_area'].get(a, 0)}</li>"
up = "".join(li(a) for d, a in moves[:5] if d > 0)
down = "".join(li(a) for d, a in moves[::-1][:5] if d < 0)
yline = f"<li>{pc(cur['total'], yr['total'])} on {mname(yr['month'])} ({yr['total']:,})</li>" if yr else ""
body = f"""<p><b>{cur['total']:,}</b> crimes recorded in Flintshire in {mname(cur['month'])}.</p>
<ul><li>{pc(cur['total'], prev['total'])} on {mname(prev['month'])} ({prev['total']:,})</li>
<li>{pc(cur['total'], avg)} on the 12-month average ({avg:,.0f})</li>{yline}</ul>
<h3>By type</h3><table cellpadding=4 style="border-collapse:collapse"><tr><th align=left>Type</th><th>{mname(cur['month'])[:3]}</th><th>{mname(prev['month'])[:3]}</th></tr>{rows}</table>
<h3>Biggest rises by area</h3><ul>{up or "<li>None</li>"}</ul>
<h3>Biggest falls by area</h3><ul>{down or "<li>None</li>"}</ul>
<p>Dashboard: <a href="https://www.deeside.com/flintshire-crime/">deeside.com/flintshire-crime</a>. Source: data.police.uk.</p>"""
subj = f"Flintshire crime figures: {mname(cur['month'])}, {cur['total']:,} crimes ({pc(cur['total'], prev['total'])} on {mname(prev['month'])[:-5]})"
p = subprocess.run(["ssh", "-o", "ConnectTimeout=20", "root@65.109.143.26",
                    "cd /opt/deeside && python3 -c 'import sys,json,alert_mail; d=json.load(sys.stdin); alert_mail.send(d[0], d[1])'"],
                   input=json.dumps([subj, body]), text=True, capture_output=True)
print("email sent" if p.returncode == 0 else "email failed: " + p.stderr[-300:])
