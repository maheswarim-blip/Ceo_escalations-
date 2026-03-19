"""
Generate a standalone HTML report of CEO escalation emails.
Open the output file directly in any browser — no server needed.
"""
import json
import html
from pathlib import Path
from datetime import datetime

data = json.loads(Path("ceo_escalation_emails_enriched.json").read_text())
emails = data["emails"]

def badge(label):
    color = {"UNREAD": "#ff4b4b", "IMPORTANT": "#ffa500", "SENT": "#4caf50", "INBOX": "#2196f3", "DRAFT": "#9e9e9e"}.get(label, "#888")
    return f'<span style="background:{color};color:#fff;padding:2px 7px;border-radius:10px;font-size:11px;margin-right:3px">{html.escape(label)}</span>'

def card(e, i):
    subj = html.escape(e.get("subject","(no subject)"))
    frm  = html.escape(e.get("from",""))
    date = html.escape(e.get("date",""))
    snip = html.escape(e.get("snippet",""))
    labels = "".join(badge(l) for l in e.get("labels",[]))
    orders = e.get("order_ids", [])
    order_tags = "".join(f'<span style="background:#1a237e;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;margin-right:3px">{o}</span>' for o in orders)

    vsm_html = ""
    for o in e.get("vsm_orders", []):
        status_color = {"DELIVERED":"#4caf50","PARTIALLY_DELIVERED":"#ff9800","PENDING":"#f44336","CANCELLED":"#9e9e9e"}.get(o.get("status",""), "#888")
        vsm_html += f'''
        <div style="background:#1a237e22;border-radius:6px;padding:8px 12px;margin:4px 0;font-size:12px">
          <b>Order {html.escape(str(o.get("order_id","")))}:</b>
          <span style="color:{status_color};font-weight:600">{html.escape(str(o.get("status","")))}</span> |
          Customer: {html.escape(str(o.get("customer_name","")))} |
          Amount: ₹{html.escape(str(o.get("total_amount","")))} |
          Issue: {html.escape(str(o.get("issue_type","")))}<br>
          <span style="color:#aaa">{html.escape(str(o.get("product_name","")))}</span>
        </div>'''

    crm_html = ""
    crm_raw = e.get("crm_comments", {})
    crm_list = []
    if isinstance(crm_raw, list):
        crm_list = crm_raw
    elif isinstance(crm_raw, dict):
        for v in crm_raw.values():
            if isinstance(v, list):
                crm_list.extend(v)
    for c in crm_list[:3]:
        crm_html += f'<div style="border-left:3px solid #4caf50;padding:4px 8px;margin:4px 0;font-size:12px;color:#ccc">{html.escape(str(c.get("comment","")))} <span style="color:#888;font-size:10px">— {html.escape(str(c.get("agent","")))} ({html.escape(str(c.get("timestamp","")[:10]))})</span></div>'

    rca = html.escape(e.get("rca_summary",""))
    rca_section = f'<div style="background:#1b5e2022;border-left:4px solid #4caf50;border-radius:4px;padding:10px;margin-top:10px;font-size:13px;color:#c8e6c9"><b>🧠 RCA Summary:</b><br>{rca}</div>' if rca else '<div style="color:#888;font-size:12px;margin-top:8px">No RCA generated yet</div>'

    return f'''
    <div style="background:#1e1e2e;border-radius:8px;padding:16px;margin-bottom:12px;border-left:4px solid #3f51b5">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div style="flex:1">
          <div style="font-weight:600;font-size:14px;color:#e0e0e0;margin-bottom:4px">{subj}</div>
          <div style="color:#888;font-size:12px">From: {frm} &nbsp;|&nbsp; {date}</div>
        </div>
        <div style="text-align:right;min-width:120px">{labels}</div>
      </div>
      <div style="color:#b0b0b0;font-size:12px;margin:8px 0;font-style:italic">"{snip}"</div>
      {f'<div style="margin:6px 0">{order_tags}</div>' if order_tags else ""}
      {f'<details style="margin-top:8px"><summary style="cursor:pointer;color:#7986cb;font-size:12px">📦 Order Details</summary>{vsm_html}</details>' if vsm_html else ""}
      {f'<details style="margin-top:4px"><summary style="cursor:pointer;color:#81c784;font-size:12px">💬 CRM Comments</summary>{crm_html}</details>' if crm_html else ""}
      {rca_section}
    </div>'''

cards_html = "\n".join(card(e, i) for i, e in enumerate(emails))

html_out = f'''<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>CEO Escalations Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #121212; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 24px; }}
    h1 {{ color: #ff4b4b; margin-bottom: 4px; }}
    .stats {{ display: flex; gap: 16px; margin: 16px 0 24px; }}
    .stat {{ background: #1e1e2e; border-radius: 8px; padding: 12px 20px; text-align: center; }}
    .stat-num {{ font-size: 28px; font-weight: 700; color: #7986cb; }}
    .stat-label {{ font-size: 12px; color: #888; }}
    input {{ background: #1e1e2e; border: 1px solid #333; color: #e0e0e0; padding: 8px 14px; border-radius: 6px; width: 100%; max-width: 500px; font-size: 14px; margin-bottom: 16px; }}
    #cards > div {{ display: block; }}
  </style>
</head>
<body>
  <h1>🔺 CEO Escalations Dashboard</h1>
  <div style="color:#888;font-size:13px;margin-top:4px">Total: {len(emails)} emails collected &nbsp;|&nbsp; As of {data.get("collected_at","")}</div>
  <div class="stats">
    <div class="stat"><div class="stat-num">{len(emails)}</div><div class="stat-label">Total Emails</div></div>
    <div class="stat"><div class="stat-num">{data.get("total_unique_orders",0)}</div><div class="stat-label">Unique Orders</div></div>
    <div class="stat"><div class="stat-num">{sum(1 for e in emails if "UNREAD" in e.get("labels",[]))}</div><div class="stat-label">Unread</div></div>
    <div class="stat"><div class="stat-num">{sum(1 for e in emails if e.get("rca_summary"))}</div><div class="stat-label">RCA Generated</div></div>
  </div>
  <input type="text" id="search" placeholder="Search by subject, from, order ID..." oninput="filterCards()">
  <div id="cards">{cards_html}</div>
  <script>
    function filterCards() {{
      const q = document.getElementById("search").value.toLowerCase();
      document.querySelectorAll("#cards > div").forEach(d => {{
        d.style.display = q === "" || d.innerText.toLowerCase().includes(q) ? "block" : "none";
      }});
    }}
  </script>
</body>
</html>'''

Path("ceo_escalation_report.html").write_text(html_out, encoding="utf-8")
print(f"Generated report with {len(emails)} emails")
