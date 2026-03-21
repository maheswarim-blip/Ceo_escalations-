"""
Generate a standalone HTML report of CEO escalation emails.
Open the output file directly in any browser — no server needed.
"""
import json
import html as html_mod
import re
from pathlib import Path
from datetime import date, datetime
from collections import defaultdict
from email.utils import parsedate_to_datetime

data = json.loads(Path("ceo_escalation_emails_enriched.json").read_text())
emails = data["emails"]

# ── date parsing ─────────────────────────────────────────────────────────────

def parse_date(date_str):
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None

# ── theme detection ──────────────────────────────────────────────────────────

THEMES = {
    "RCA Request":         r"\brca\b|root cause",
    "Delivery Delay":      r"delay|not (received|arrived|delivered)|pending dispatch|not dispatched",
    "Frame Damage":        r"frame.{0,20}(damage|broken|crack|quality)|damage.{0,20}frame|phonic|broken",
    "Lens / Prescription": r"\blens\b|prescription|progressive|power mismatch|rodenstock|corridor|coating",
    "Refund":              r"refund|money (not|back)|payment issue",
    "Wrong Product":       r"wrong (product|item|frame|lens)|mismatch",
    "Legal Threat":        r"legal|consumer court|\bfir\b|police",
    "Exchange / Return":   r"\bexchange\b|\breturn\b|replacement",
    "HTO / Visit":         r"\bhto\b|home (eye )?test|visit required|appointment",
    "LinkedIn / Social":   r"linkedin|social media|twitter|instagram",
    "Process Gap":         r"\bgap\b|idle time|sla breach|delay in (case|handling)",
}

def get_themes(subject, snippet=""):
    text = (subject + " " + (snippet or "")).lower()
    matched = [name for name, pat in THEMES.items() if re.search(pat, text, re.I)]
    return matched if matched else ["Other"]

# ── aggregate by time & theme ────────────────────────────────────────────────

per_day   = defaultdict(int)
per_week  = defaultdict(int)
per_month = defaultdict(int)
theme_counts = defaultdict(int)
unread_count = 0
rca_count = 0

for e in emails:
    dt = parse_date(e.get("date", ""))
    if dt:
        per_day[dt.strftime("%Y-%m-%d")] += 1
        y, w, _ = dt.isocalendar()
        per_week[f"{y}-W{w:02d}"] += 1
        per_month[dt.strftime("%Y-%m")] += 1
    for t in get_themes(e.get("subject", ""), e.get("snippet", "")):
        theme_counts[t] += 1
    if "UNREAD" in e.get("labels", []):
        unread_count += 1
    if e.get("rca_summary"):
        rca_count += 1

sorted_days   = sorted(per_day.items())
sorted_weeks  = sorted(per_week.items())
sorted_months = sorted(per_month.items())
sorted_themes = sorted(theme_counts.items(), key=lambda x: -x[1])

today_str       = date.today().strftime("%Y-%m-%d")
y0, w0, _       = date.today().isocalendar()
this_week_key   = f"{y0}-W{w0:02d}"
this_month_key  = date.today().strftime("%Y-%m")
today_count      = per_day.get(today_str, 0)
this_week_count  = per_week.get(this_week_key, 0)
this_month_count = per_month.get(this_month_key, 0)

# ── helpers ──────────────────────────────────────────────────────────────────

LABEL_COLORS = {
    "UNREAD": "#ff4b4b", "IMPORTANT": "#ffa500",
    "SENT": "#4caf50",   "INBOX": "#2196f3", "DRAFT": "#9e9e9e",
}

def badge(label):
    color = LABEL_COLORS.get(label, "#555")
    return f'<span class="badge" style="background:{color}">{html_mod.escape(label)}</span>'

# ── card builder ─────────────────────────────────────────────────────────────

def build_card(e):
    subj   = html_mod.escape(e.get("subject", "(no subject)"))
    frm    = html_mod.escape(e.get("from", ""))
    date_s = html_mod.escape(e.get("date", ""))
    snip   = html_mod.escape(e.get("snippet", ""))
    labels = "".join(badge(l) for l in e.get("labels", [])
                     if l not in ("CATEGORY_FORUMS", "CATEGORY_UPDATES", "CATEGORY_PROMOTIONS"))

    orders     = e.get("order_ids", [])
    order_tags = "".join(f'<span class="order-tag">{o}</span>' for o in orders)

    valid_vsm = [o for o in e.get("vsm_orders", []) if "error" not in o]
    vsm_rows  = ""
    for o in valid_vsm:
        status = str(o.get("status", ""))
        sc = {"DELIVERED": "#4caf50", "PARTIALLY_DELIVERED": "#ff9800",
              "CANCELLED": "#9e9e9e", "ON_HOLD": "#ff5722", "RETURNED": "#e91e63"}.get(status, "#888")
        vsm_rows += (
            f'<div class="order-row">'
            f'<b>#{html_mod.escape(str(o.get("order_id","")))}:</b> '
            f'<span style="color:{sc}">{html_mod.escape(status)}</span> · '
            f'{html_mod.escape(str(o.get("customer_name","")))} · '
            f'₹{html_mod.escape(str(o.get("total_amount","")))} · '
            f'{html_mod.escape(str(o.get("issue_type","")))}'
            f'</div>'
        )

    crm_list = []
    crm_raw  = e.get("crm_comments", {})
    if isinstance(crm_raw, list):
        crm_list = [c for c in crm_raw if "error" not in c]
    elif isinstance(crm_raw, dict):
        for v in crm_raw.values():
            if isinstance(v, list):
                crm_list.extend(c for c in v if "error" not in c)

    crm_rows = ""
    for c in crm_list[:4]:
        msg    = html_mod.escape(str(c.get("message", c.get("comment", ""))))
        author = html_mod.escape(str(c.get("author", c.get("agent", "—"))))
        ts     = str(c.get("created_at", c.get("timestamp", "")))[:10]
        direction = c.get("direction", "")
        side_color = "#4caf50" if direction == "inbound" else "#7986cb"
        crm_rows += (
            f'<div class="crm-row" style="border-left-color:{side_color}">'
            f'{msg} <span class="crm-meta">— {author} ({ts})</span></div>'
        )

    rca = html_mod.escape(e.get("rca_summary", ""))
    rca_block = (
        f'<div class="rca-box">🧠 <b>RCA:</b> {rca}</div>'
        if rca else ""
    )

    email_themes = get_themes(e.get("subject", ""), e.get("snippet", ""))
    theme_tags   = "".join(f'<span class="theme-tag">{t}</span>' for t in email_themes)

    search_text = html_mod.escape(
        (e.get("subject", "") + " " + e.get("from", "") + " " + " ".join(orders) + " " + " ".join(email_themes)).lower()
    )

    vsm_section = (
        f'<details><summary class="det-sum order-det">📦 Orders ({len(valid_vsm)})</summary>'
        f'{vsm_rows}</details>'
        if vsm_rows else ""
    )
    crm_section = (
        f'<details><summary class="det-sum crm-det">💬 CRM thread ({len(crm_list)} comments)</summary>'
        f'{crm_rows}</details>'
        if crm_rows else ""
    )

    return f'''<div class="card" data-search="{search_text}">
  <div class="card-header">
    <div class="card-left">
      <div class="card-subject">{subj}</div>
      <div class="card-meta">From: {frm} · {date_s}</div>
      <div class="card-tags">{theme_tags}{order_tags}</div>
    </div>
    <div class="card-labels">{labels}</div>
  </div>
  <div class="card-snippet">"{snip}"</div>
  {vsm_section}{crm_section}{rca_block}
</div>'''

cards_html = "\n".join(build_card(e) for e in emails)

# ── JS data ───────────────────────────────────────────────────────────────────

def jsl(pairs): return json.dumps([x[0] for x in pairs])
def jsv(pairs): return json.dumps([x[1] for x in pairs])

# ── HTML ─────────────────────────────────────────────────────────────────────

html_out = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>CEO Escalations Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0d0d1a;
      color: #d0d0e0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      padding: 28px 36px;
      line-height: 1.5;
    }}

    /* ── Header ── */
    .header {{ margin-bottom: 20px; }}
    h1 {{ font-size: 22px; font-weight: 700; color: #ff4b4b; letter-spacing: -0.3px; }}
    .subtitle {{ color: #555; font-size: 12px; margin-top: 3px; }}

    /* ── Stat row ── */
    .stats {{
      display: flex; flex-wrap: wrap; gap: 10px;
      margin: 18px 0 24px;
    }}
    .stat {{
      background: #161625; border: 1px solid #22223a;
      border-radius: 10px; padding: 12px 18px;
      text-align: center; min-width: 100px;
    }}
    .stat-num {{ font-size: 28px; font-weight: 700; color: #7986cb; line-height: 1; }}
    .stat-label {{ font-size: 10px; color: #555; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.6px; }}
    .stat-today .stat-num {{ color: #ff7043; }}
    .stat-week  .stat-num {{ color: #ffa726; }}
    .stat-month .stat-num {{ color: #66bb6a; }}

    /* ── Overview panel ── */
    .overview {{
      background: #13132a; border: 1px solid #1e1e38;
      border-radius: 12px; padding: 20px 24px;
      margin-bottom: 28px;
    }}
    .overview-title {{
      font-size: 11px; font-weight: 600; color: #555;
      text-transform: uppercase; letter-spacing: 1px;
      margin-bottom: 18px;
    }}
    .charts-grid {{
      display: grid; grid-template-columns: 3fr 2fr; gap: 16px;
    }}
    .chart-panel {{
      background: #0d0d1a; border: 1px solid #1a1a30;
      border-radius: 8px; padding: 16px;
    }}
    .chart-panel h3 {{
      font-size: 10px; color: #555;
      text-transform: uppercase; letter-spacing: 0.8px;
      margin-bottom: 10px;
    }}

    /* Tabs */
    .tabs {{ display: flex; gap: 4px; margin-bottom: 10px; }}
    .tab {{
      background: #1a1a30; border: 1px solid #22223a; color: #555;
      padding: 4px 12px; border-radius: 5px; cursor: pointer;
      font-size: 11px; transition: all 0.15s;
    }}
    .tab:hover {{ color: #aaa; }}
    .tab.active {{ background: #3949ab; border-color: #3949ab; color: #fff; }}
    .chart-container {{ position: relative; height: 170px; }}

    /* Theme bars */
    .theme-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 7px; }}
    .theme-label {{
      font-size: 11px; color: #888; min-width: 145px;
      text-align: right; white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis;
    }}
    .theme-bar-bg {{
      flex: 1; background: #1a1a30; border-radius: 3px; height: 14px;
      overflow: hidden;
    }}
    .theme-bar-fill {{
      height: 100%; border-radius: 3px;
      background: linear-gradient(90deg, #283593, #7986cb);
    }}
    .theme-count {{ font-size: 11px; color: #7986cb; min-width: 26px; text-align: right; }}

    /* ── Search ── */
    .search-area {{ margin-bottom: 14px; }}
    input[type=text] {{
      background: #13132a; border: 1px solid #22223a; color: #d0d0e0;
      padding: 9px 14px; border-radius: 8px;
      width: 100%; max-width: 520px; font-size: 13px; outline: none;
    }}
    input[type=text]:focus {{ border-color: #3949ab; }}
    .result-info {{ font-size: 11px; color: #444; margin-top: 6px; }}

    /* ── Section heading ── */
    .section-heading {{
      font-size: 10px; color: #333; text-transform: uppercase;
      letter-spacing: 1px; margin-bottom: 12px;
      padding-bottom: 6px; border-bottom: 1px solid #161625;
    }}

    /* ── Cards ── */
    .card {{
      background: #13132a; border-radius: 10px;
      padding: 14px 16px; margin-bottom: 8px;
      border-left: 3px solid #283593;
      transition: border-color 0.15s;
    }}
    .card:hover {{ border-left-color: #7986cb; }}
    .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }}
    .card-left {{ flex: 1; min-width: 0; }}
    .card-subject {{
      font-weight: 600; font-size: 13px; color: #e0e0e0;
      margin-bottom: 2px; line-height: 1.4;
    }}
    .card-meta {{ color: #444; font-size: 11px; margin-bottom: 5px; }}
    .card-tags {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }}
    .card-labels {{ display: flex; flex-wrap: wrap; gap: 3px; justify-content: flex-end; min-width: 80px; }}
    .card-snippet {{ color: #666; font-size: 11px; margin: 8px 0; font-style: italic; line-height: 1.5; }}

    /* Tags & badges */
    .badge {{ padding: 2px 6px; border-radius: 8px; font-size: 10px; }}
    .order-tag {{
      background: #111830; color: #7986cb;
      padding: 1px 7px; border-radius: 4px;
      font-size: 10px; font-family: monospace;
      border: 1px solid #1e2a50;
    }}
    .theme-tag {{
      background: #0f1f12; color: #66bb6a;
      padding: 1px 7px; border-radius: 8px;
      font-size: 10px; border: 1px solid #1a3a1e;
    }}

    /* Details */
    details {{ margin-top: 6px; }}
    .det-sum {{ cursor: pointer; font-size: 11px; padding: 2px 0; user-select: none; }}
    .order-det {{ color: #7986cb; }}
    .crm-det   {{ color: #66bb6a; }}
    .order-row {{
      background: #0d0d1a; border-radius: 5px;
      padding: 5px 10px; margin: 3px 0; font-size: 11px; color: #aaa;
    }}
    .crm-row {{
      border-left: 3px solid #4caf50; padding: 4px 8px;
      margin: 3px 0; font-size: 11px; color: #aaa;
    }}
    .crm-meta {{ color: #444; font-size: 10px; }}

    /* RCA */
    .rca-box {{
      background: #0f2218; border-left: 3px solid #4caf50;
      border-radius: 4px; padding: 8px 12px;
      margin-top: 8px; font-size: 11px; color: #a5d6a7;
    }}
  </style>
</head>
<body>

  <!-- Header -->
  <div class="header">
    <h1>🔺 CEO Escalations Dashboard</h1>
    <div class="subtitle">
      {len(emails)} escalations &nbsp;·&nbsp;
      {data.get("total_unique_orders", 0)} unique orders &nbsp;·&nbsp;
      Data as of {data.get("collected_at", "")}
    </div>
  </div>

  <!-- Stat cards -->
  <div class="stats">
    <div class="stat"><div class="stat-num">{len(emails)}</div><div class="stat-label">Total</div></div>
    <div class="stat stat-today"><div class="stat-num">{today_count}</div><div class="stat-label">Today</div></div>
    <div class="stat stat-week"><div class="stat-num">{this_week_count}</div><div class="stat-label">This Week</div></div>
    <div class="stat stat-month"><div class="stat-num">{this_month_count}</div><div class="stat-label">This Month</div></div>
    <div class="stat"><div class="stat-num">{unread_count}</div><div class="stat-label">Unread</div></div>
    <div class="stat"><div class="stat-num">{data.get("total_unique_orders", 0)}</div><div class="stat-label">Orders</div></div>
    <div class="stat"><div class="stat-num">{rca_count}</div><div class="stat-label">RCA Done</div></div>
  </div>

  <!-- Overview -->
  <div class="overview">
    <div class="overview-title">📊 Overview</div>
    <div class="charts-grid">

      <!-- Time chart -->
      <div class="chart-panel">
        <h3>Escalations over time</h3>
        <div class="tabs">
          <button class="tab active" onclick="switchTime('day', this)">Daily</button>
          <button class="tab" onclick="switchTime('week', this)">Weekly</button>
          <button class="tab" onclick="switchTime('month', this)">Monthly</button>
        </div>
        <div class="chart-container">
          <canvas id="timeChart"></canvas>
        </div>
      </div>

      <!-- Theme breakdown -->
      <div class="chart-panel">
        <h3>Issue themes</h3>
        <div id="themeBars"></div>
      </div>

    </div>
  </div>

  <!-- Search -->
  <div class="search-area">
    <input type="text" id="search"
      placeholder="🔍  Search by subject, sender, order ID, theme..."
      oninput="filterCards()">
    <div class="result-info" id="resultInfo">{len(emails)} escalations</div>
  </div>

  <div class="section-heading">All Escalations</div>
  <div id="cards">{cards_html}</div>

  <script>
    // ── Chart data ────────────────────────────────────────────────────────────
    const DATA = {{
      day:   {{ labels: {jsl(sorted_days)},   values: {jsv(sorted_days)} }},
      week:  {{ labels: {jsl(sorted_weeks)},  values: {jsv(sorted_weeks)} }},
      month: {{ labels: {jsl(sorted_months)}, values: {jsv(sorted_months)} }},
    }};
    const THEME_LABELS = {jsl(sorted_themes)};
    const THEME_VALUES = {jsv(sorted_themes)};

    // ── Time chart ────────────────────────────────────────────────────────────
    Chart.defaults.color = "#555";
    const ctx  = document.getElementById("timeChart").getContext("2d");
    const grid = {{ color: "#161625" }};
    let timeChart = new Chart(ctx, {{
      type: "bar",
      data: {{
        labels: DATA.day.labels,
        datasets: [{{
          data: DATA.day.values,
          backgroundColor: "rgba(121,134,203,0.65)",
          borderColor: "#7986cb",
          borderWidth: 1,
          borderRadius: 3,
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{
          title: ctx => ctx[0].label,
          label: ctx => ` ${{ctx.parsed.y}} escalation${{ctx.parsed.y !== 1 ? "s" : ""}}`,
        }} }} }},
        scales: {{
          x: {{ ticks: {{ maxRotation: 45, font: {{ size: 9 }} }}, grid }},
          y: {{ ticks: {{ font: {{ size: 10 }}, stepSize: 1 }}, grid, beginAtZero: true }},
        }},
      }},
    }});

    function switchTime(mode, btn) {{
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      btn.classList.add("active");
      timeChart.data.labels             = DATA[mode].labels;
      timeChart.data.datasets[0].data   = DATA[mode].values;
      timeChart.update();
    }}

    // ── Theme bars ────────────────────────────────────────────────────────────
    const maxVal = Math.max(...THEME_VALUES, 1);
    document.getElementById("themeBars").innerHTML =
      THEME_LABELS.map((lbl, i) => {{
        const pct = Math.round((THEME_VALUES[i] / maxVal) * 100);
        return `<div class="theme-row">
          <div class="theme-label">${{lbl}}</div>
          <div class="theme-bar-bg">
            <div class="theme-bar-fill" style="width:${{pct}}%"></div>
          </div>
          <div class="theme-count">${{THEME_VALUES[i]}}</div>
        </div>`;
      }}).join("");

    // ── Search / filter ───────────────────────────────────────────────────────
    const allCards = Array.from(document.querySelectorAll("#cards .card"));
    function filterCards() {{
      const q = document.getElementById("search").value.toLowerCase().trim();
      let shown = 0;
      allCards.forEach(c => {{
        const hit = !q || c.dataset.search.includes(q) || c.innerText.toLowerCase().includes(q);
        c.style.display = hit ? "" : "none";
        if (hit) shown++;
      }});
      document.getElementById("resultInfo").textContent =
        q ? `${{shown}} of {len(emails)} escalations` : `{len(emails)} escalations`;
    }}
  </script>
</body>
</html>'''

Path("ceo_escalation_report.html").write_text(html_out, encoding="utf-8")
print(f"✓ Report generated: {len(emails)} emails | {len(sorted_months)} months | {len(sorted_themes)} themes")
