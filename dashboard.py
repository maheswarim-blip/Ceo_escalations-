"""
CEO Escalation Dashboard
========================
Streamlit app showing CEO escalation cases enriched with VSM order data,
CRM comments, and Claude-generated RCA.

Run:
    streamlit run dashboard.py
"""

import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dotenv import load_dotenv
from pathlib import Path

import streamlit as st

from case_builder import build_cases

load_dotenv()

# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CEO Escalations Dashboard",
    page_icon="🔺",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* sidebar case cards */
.case-card {
    background: #1e1e2e;
    border-left: 4px solid #666;
    border-radius: 6px;
    padding: 10px 12px;
    margin-bottom: 8px;
    cursor: pointer;
}
.case-card.HIGH  { border-left-color: #ff4b4b; }
.case-card.MEDIUM{ border-left-color: #ffa500; }
.case-card.LOW   { border-left-color: #00c853; }

.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    margin-right: 4px;
}
.badge-HIGH   { background:#ff4b4b22; color:#ff4b4b; border:1px solid #ff4b4b44; }
.badge-MEDIUM { background:#ffa50022; color:#ffa500; border:1px solid #ffa50044; }
.badge-LOW    { background:#00c85322; color:#00c853; border:1px solid #00c85344; }
.badge-status { background:#3a3a5a; color:#aaa; border:1px solid #555; }

/* order cards */
.order-card {
    background:#16213e;
    border:1px solid #2a2a4a;
    border-radius:8px;
    padding:14px 16px;
    margin-bottom:12px;
}
/* chat bubbles */
.bubble-in {
    background:#1a3a5a;
    border-radius:12px 12px 12px 2px;
    padding:8px 12px;
    margin:4px 0 4px 0;
    max-width:85%;
    display:inline-block;
    font-size:13px;
}
.bubble-out {
    background:#1a3a1a;
    border-radius:12px 12px 2px 12px;
    padding:8px 12px;
    margin:4px 0 4px auto;
    max-width:85%;
    display:inline-block;
    text-align:right;
    font-size:13px;
    float:right;
    clear:both;
}
.clearfix { clear:both; }
.ts { font-size:10px; color:#888; }
</style>
""", unsafe_allow_html=True)

# ── data loading ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=120, show_spinner=False)
def load_cases():
    enriched = Path("ceo_escalation_emails_enriched.json")
    raw      = Path("ceo_escalation_emails.json")

    if enriched.exists():
        with open(enriched) as f:
            data = json.load(f)
    elif raw.exists():
        with open(raw) as f:
            data = json.load(f)
    else:
        return [], None, {}

    meta = {
        "total": data.get("total_collected", len(data.get("emails", []))),
        "synced_at": data.get("synced_at") or data.get("collected_at") or "",
    }
    return build_cases(data["emails"]), data.get("enriched_at") or data.get("collected_at"), meta


def _sync_available() -> bool:
    token_file = os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE", "")
    return bool(token_file and os.path.exists(token_file))


def _do_full_sync():
    """Run Gmail sync + body fetch + VSM enrichment, then clear the cache."""
    try:
        from pipeline import run_full_sync
        result = run_full_sync(verbose=False)
        st.session_state["last_sync_result"] = result
        load_cases.clear()
    except Exception as e:
        st.session_state["last_sync_result"] = {"error": str(e)}


# ── sidebar ────────────────────────────────────────────────────────────────────

def severity_icon(s):
    return {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟢"}.get(s, "⚪")


def render_sidebar(cases, meta=None):
    with st.sidebar:
        st.title("🔺 CEO Escalations")
        st.caption(f"{len(cases)} active cases")

        # ── Real-time sync controls ──────────────────────────────────────────
        st.divider()
        available = _sync_available()
        col_btn, col_auto = st.columns([3, 2])

        with col_btn:
            if st.button(
                "🔄 Sync Now",
                disabled=not available,
                use_container_width=True,
                type="primary",
                help=(
                    "Fetch new emails, get full bodies & enrich with VSM order data"
                    if available else
                    "Available only inside a Claude Code session"
                ),
            ):
                with st.spinner("Syncing Gmail + VSM enrichment…"):
                    _do_full_sync()
                st.rerun()

        with col_auto:
            auto = st.toggle("Auto", value=st.session_state.get("auto_refresh", False),
                             help="Full sync every 5 min")
            st.session_state["auto_refresh"] = auto

        # Show last sync result
        lsr = st.session_state.get("last_sync_result")
        if lsr:
            if "error" in lsr:
                st.error(f"Sync failed: {lsr['error']}", icon="⚠️")
            elif lsr.get("errors"):
                st.warning(
                    f"+{lsr.get('new_gmail',0)} new · {lsr.get('new_enriched',0)} enriched "
                    f"· ⚠️ {len(lsr['errors'])} warning(s)",
                    icon="🔄"
                )
            else:
                new_g = lsr.get("new_gmail", 0)
                new_e = lsr.get("new_enriched", 0)
                total = lsr.get("total", 0)
                if new_g == 0:
                    st.success(f"Up to date · {total} total emails", icon="✅")
                else:
                    st.success(
                        f"+{new_g} new emails · {new_e} enriched · {total} total",
                        icon="✅"
                    )

        if meta and meta.get("synced_at"):
            st.caption(f"Last synced: {meta['synced_at'][:19]}")

        st.divider()

        # Filters
        with st.expander("Filters", expanded=False):
            sev_filter = st.multiselect("Severity", ["HIGH","MEDIUM","LOW"],
                                        default=["HIGH","MEDIUM","LOW"])
            issue_filter = st.text_input("Issue type contains", "")

        filtered = [
            c for c in cases
            if c["severity"] in sev_filter
            and (issue_filter.lower() in c["issue_type"].lower() if issue_filter else True)
        ]

        st.markdown(f"**{len(filtered)} cases shown**")
        st.divider()

        selected_idx = st.session_state.get("selected_case_idx", 0)

        for i, case in enumerate(filtered):
            sev = case["severity"]
            icon = severity_icon(sev)
            orders_str = ", ".join(case["order_ids"][:2])
            if len(case["order_ids"]) > 2:
                orders_str += f" +{len(case['order_ids'])-2}"

            label = f"{icon} {case['title'][:42]}{'…' if len(case['title'])>42 else ''}"
            sub   = f"📦 {orders_str or 'No orders'} · {case['email_count']} emails"

            btn_type = "primary" if i == selected_idx else "secondary"
            if st.button(label, key=f"case_{i}", help=sub, use_container_width=True,
                         type=btn_type):
                st.session_state["selected_case_idx"] = i
                # Clear cached RCA for this case on re-select
                st.rerun()

    return filtered

# ── helpers ────────────────────────────────────────────────────────────────────

def status_color(status: str) -> str:
    s = (status or "").upper()
    if s in ("DELIVERED", "COMPLETE_SHIPPED"):
        return "#00c853"
    if "REFUND" in s or "RETURN" in s:
        return "#ff9800"
    if s in ("DISPATCHED", "PROCESSING", "IN_HOUSE_PROCESSING", "ORDER_PLACED"):
        return "#4fc3f7"
    if s in ("CANCELLED",):
        return "#ff4b4b"
    if s in ("CLOSED",):
        return "#888"
    return "#aaa"


def payment_color(status: str) -> str:
    s = (status or "").upper()
    if "REFUND" in s:
        return "#ff9800"
    if s == "PAID":
        return "#00c853"
    return "#888"

# ── overview helpers ────────────────────────────────────────────────────────────

def _parse_case_date(date_str: str):
    """Parse RFC 2822 or ISO date string to an aware datetime."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str[:19], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


# City → India zone mapping (extend as new store cities appear)
_CITY_TO_ZONE: dict[str, str] = {
    # North
    "Delhi": "North", "New Delhi": "North", "Gurgaon": "North", "Gurugram": "North",
    "Noida": "North", "Faridabad": "North", "Ghaziabad": "North",
    "Lucknow": "North", "Kanpur": "North", "Agra": "North", "Varanasi": "North",
    "Jaipur": "North", "Jodhpur": "North", "Udaipur": "North", "Kota": "North",
    "Chandigarh": "North", "Amritsar": "North", "Ludhiana": "North",
    "Dehradun": "North", "Meerut": "North", "Patiala": "North",
    # South
    "Bengaluru": "South", "Bangalore": "South",
    "Hyderabad": "South", "Chennai": "South", "Madras": "South",
    "Kochi": "South", "Cochin": "South", "Thiruvananthapuram": "South",
    "Coimbatore": "South", "Mysuru": "South", "Mysore": "South",
    "Visakhapatnam": "South", "Vijayawada": "South", "Mangaluru": "South",
    "Madurai": "South", "Tiruchirappalli": "South",
    # West
    "Mumbai": "West", "Pune": "West", "Ahmedabad": "West", "Surat": "West",
    "Nagpur": "West", "Indore": "West", "Bhopal": "West", "Vadodara": "West",
    "Nashik": "West", "Aurangabad": "West", "Goa": "West", "Panaji": "West",
    "Thane": "West", "Navi Mumbai": "West",
    # East
    "Kolkata": "East", "Calcutta": "East",
    "Bhubaneswar": "East", "Patna": "East", "Ranchi": "East",
    "Guwahati": "East", "Cuttack": "East", "Jamshedpur": "East",
    "Siliguri": "East", "Raipur": "East",
}


def _city_from_store(store: str) -> str:
    """Extract city name from store string like 'LKST2011 Mumbai - Mall' → 'Mumbai'."""
    if not store:
        return ""
    # Strip leading store-code prefix (e.g. LKST2011, LKST, LK)
    cleaned = re.sub(r"^[A-Z]{2,6}\d*\s*[-–]?\s*", "", store).strip()
    # Take the first token before any dash/hyphen
    city = cleaned.split("-")[0].split("–")[0].strip()
    return city


def _store_to_zone(store: str) -> str:
    """Map a VSM store string to North / South / East / West / Online / Unknown."""
    if not store:
        return "Unknown"
    store_upper = store.upper()
    if "ONLINE" in store_upper or "OMNI" in store_upper or "WEB" in store_upper:
        return "Online"
    city = _city_from_store(store)
    # Try exact match first, then partial
    if city in _CITY_TO_ZONE:
        return _CITY_TO_ZONE[city]
    for known_city, zone in _CITY_TO_ZONE.items():
        if known_city.lower() in city.lower() or city.lower() in known_city.lower():
            return zone
    return f"Unknown ({city})" if city else "Unknown"


def _build_overview_data(cases: list) -> dict:
    region_counts: Counter = Counter()
    week_counts: dict = defaultdict(int)
    month_counts: dict = defaultdict(int)
    issue_counts: Counter = Counter()
    severity_counts: Counter = Counter()

    for case in cases:
        issue_counts[case["issue_type"]] += 1
        severity_counts[case["severity"]] += 1

        # Zone from VSM store (first order that resolves to a known zone)
        zone = "Unknown"
        for order in case.get("vsm_orders", []):
            store = order.get("store", "") or ""
            z = _store_to_zone(store)
            if z not in ("Unknown", ""):
                zone = z
                break
        region_counts[zone] += 1

        dt = _parse_case_date(case.get("latest_date", ""))
        if dt:
            week_counts[dt.strftime("%Y-W%V")] += 1
            month_counts[dt.strftime("%b '%y")] += 1

    return {
        "region_counts": dict(region_counts.most_common(15)),
        "week_counts": dict(sorted(week_counts.items())),
        "month_counts": dict(sorted(month_counts.items())),
        "issue_counts": dict(issue_counts.most_common()),
        "severity_counts": dict(severity_counts),
        "total": len(cases),
    }


def _build_voc_corpus(cases: list) -> str:
    """Collect customer-facing text from up to 60 cases for VoC analysis."""
    lines = []
    for i, case in enumerate(cases[:60]):
        lines.append(
            f"\n--- CASE {i+1}: {case['issue_type']} | {case['severity']} | "
            f"{case.get('latest_date','')[:10]} ---"
        )
        emails = case.get("emails", [])
        if emails:
            first = emails[0]
            body = first.get("snippet") or ""
            if body:
                lines.append(f"Complaint snippet: {body[:600]}")

        for order in case.get("vsm_orders", [])[:2]:
            status_raw = order.get("status", {})
            status_str = (
                status_raw.get("status") if isinstance(status_raw, dict) else str(status_raw or "")
            )
            store = order.get("store", "")
            amt = order.get("total_amount")
            payment_raw = order.get("payment_status", {})
            payment_str = (
                payment_raw.get("status") if isinstance(payment_raw, dict) else str(payment_raw or "")
            )
            zone = _store_to_zone(store) if store else ""
            line_parts = []
            if status_str:
                line_parts.append(f"status={status_str}")
            if store:
                line_parts.append(f"store={store}")
            if zone and zone != "Unknown":
                line_parts.append(f"zone={zone}")
            if amt:
                line_parts.append(f"value=₹{amt:,.0f}")
            if payment_str:
                line_parts.append(f"payment={payment_str}")
            if line_parts:
                lines.append("VSM: " + " | ".join(line_parts))

        crm = case.get("crm_comments", {})
        for _, comments in list(crm.items())[:1]:
            inbound_msgs = [
                c.get("message", "")
                for c in comments
                if c.get("direction") == "inbound" and c.get("message")
            ][:3]
            if inbound_msgs:
                joined = " | ".join(m[:200] for m in inbound_msgs)
                lines.append(f"Customer said: {joined}")

    return "\n".join(lines)


_VOC_SYSTEM = (
    "You are a Customer Experience analyst at Lenskart. "
    "Be crisp — bullets and short sentences only. No padding."
)

_VOC_PROMPT = """Analyse this corpus of CEO escalation cases and produce a Voice of Customer report.

Output exactly these four sections using markdown:

## Top Complaint Themes
List up to 7 themes. For each: **Theme name** — count estimate, one-line description, and the VSM order status most commonly linked to it.

## Customer Sentiment Snapshot
- Overall dominant tone (frustrated / angry / disappointed / neutral)
- 3 key emotional triggers pulled directly from the complaint text

## Systemic Patterns
3–5 process or operational gaps that recur across multiple cases. Quote specific facts from the data.

## Urgent Actions (Next 24–48 Hours)
Top 4 actions for the CX/Ops team. Each: team responsible + action + expected customer outcome.

---
CASE DATA:
{corpus}
"""


def _make_anthropic_client():
    import anthropic
    import httpx as _httpx
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if os.environ.get("ANTHROPIC_DISABLE_SSL_VERIFY", "").lower() in ("1", "true", "yes"):
        http_client = _httpx.Client(verify=False)
    elif ca:
        http_client = _httpx.Client(verify=ca)
    else:
        http_client = None
    kwargs = {"api_key": os.environ.get("ANTHROPIC_API_KEY", "")}
    if http_client:
        kwargs["http_client"] = http_client
    return anthropic.Anthropic(**kwargs)


def render_voc_analysis(cases: list):
    st.subheader("🎙️ Voice of Customer Analysis")
    voc_key = "voc_analysis"
    existing = st.session_state.get(voc_key)
    api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

    col1, col2 = st.columns([1, 5])
    with col1:
        gen_btn = st.button(
            "✨ Analyse VoC" if not existing else "🔄 Re-analyse",
            disabled=not api_key_set,
            type="primary",
            use_container_width=True,
            key="voc_btn",
        )
        if not api_key_set:
            st.caption("⚠️ Set `ANTHROPIC_API_KEY`")
    with col2:
        st.caption(
            f"Claude Sonnet analyses all {len(cases)} cases — complaint emails + VSM order data "
            "+ CRM logs — to surface themes, sentiment, and systemic patterns."
        )

    if gen_btn and api_key_set:
        corpus = _build_voc_corpus(cases)
        prompt = _VOC_PROMPT.format(corpus=corpus)
        _client = _make_anthropic_client()
        placeholder = st.empty()
        full_text = ""
        with st.spinner("Analysing Voice of Customer across all cases…"):
            try:
                with _client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=2000,
                    system=_VOC_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                ) as stream:
                    for event in stream:
                        if event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                full_text += event.delta.text
                                placeholder.markdown(full_text + "▌")
                placeholder.markdown(full_text)
                st.session_state[voc_key] = full_text
            except Exception as e:
                st.error(f"VoC analysis failed: {e}")
    elif existing:
        st.markdown(existing)
    else:
        st.info(
            "Click **Analyse VoC** to have Claude identify themes, sentiment, and systemic "
            "patterns across all escalation cases."
        )


def render_overview_tab(cases: list, filtered_cases: list):
    # ── Search ──────────────────────────────────────────────────────────────────
    st.subheader("🔍 Search Cases")
    q = st.text_input(
        "Search by case number, order ID, or keyword in title",
        placeholder="e.g. 100123456  ·  1000987654  ·  'lens damage'",
        key="overview_search",
    ).strip().lower()

    if q:
        hits = [
            c for c in cases
            if q in (c.get("case_number") or "").lower()
            or any(q in oid for oid in c.get("order_ids", []))
            or q in c.get("title", "").lower()
        ]
        if hits:
            st.success(f"{len(hits)} case(s) found")
            for case in hits:
                sev = case["severity"]
                sev_color = {"HIGH": "#ff4b4b", "MEDIUM": "#ffa500", "LOW": "#00c853"}.get(sev, "#888")
                with st.expander(
                    f"{severity_icon(sev)} {case['title'][:70]}",
                    expanded=len(hits) == 1,
                ):
                    ca, cb, cc = st.columns([2, 2, 1])
                    with ca:
                        st.write(f"**Case #:** {case.get('case_number') or '—'}")
                        st.write(f"**Issue:** {case['issue_type']}")
                        st.write(f"**Orders:** {', '.join(case['order_ids']) or '—'}")
                    with cb:
                        st.markdown(
                            f"**Severity:** <span style='color:{sev_color};font-weight:700'>{sev}</span>",
                            unsafe_allow_html=True,
                        )
                        st.write(f"**Emails:** {case['email_count']}")
                        st.write(f"**Last activity:** {case['latest_date'][:16] if case['latest_date'] else '—'}")
                        stores = list({
                            o.get("store", "") for o in case.get("vsm_orders", []) if o.get("store")
                        })
                        if stores:
                            st.write(f"**Store(s):** {', '.join(stores[:3])}")
                        zones = list({
                            _store_to_zone(s) for s in stores if _store_to_zone(s) not in ("Unknown", "")
                        })
                        if zones:
                            st.write(f"**Zone:** {', '.join(zones)}")
                    with cc:
                        # Find index in filtered_cases for navigation
                        try:
                            nav_idx = filtered_cases.index(case)
                        except ValueError:
                            nav_idx = None
                        if nav_idx is not None:
                            if st.button("Open →", key=f"open_{case['thread_id']}", type="primary"):
                                st.session_state["selected_case_idx"] = nav_idx
                                st.rerun()
                        else:
                            st.caption("_Not in current filter_")
        else:
            st.warning("No cases match your search.")

    st.divider()

    # ── Summary metrics ──────────────────────────────────────────────────────────
    data = _build_overview_data(cases)
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Cases", data["total"])
    m2.metric("High Severity", data["severity_counts"].get("HIGH", 0))
    m3.metric("Medium", data["severity_counts"].get("MEDIUM", 0))
    m4.metric("Low", data["severity_counts"].get("LOW", 0))
    m5.metric("Issue Types", len(data["issue_counts"]))

    st.divider()

    # ── Charts ───────────────────────────────────────────────────────────────────
    st.subheader("📊 Case Analytics")

    # Row 1: Region | Issue Type
    col_r, col_i = st.columns(2)
    with col_r:
        st.markdown("**Cases by Zone** _(North / South / East / West / Online)_")
        if data["region_counts"]:
            # Sort in a fixed cardinal order for readability
            ordered_zones = ["North", "South", "East", "West", "Online"]
            zone_data = {
                z: data["region_counts"][z]
                for z in ordered_zones
                if z in data["region_counts"]
            }
            # Append any unexpected values (Unknown etc.) at the end
            for k, v in data["region_counts"].items():
                if k not in zone_data:
                    zone_data[k] = v
            st.bar_chart(zone_data, height=260)
        else:
            st.info("No region data — VSM store field empty.")

    with col_i:
        st.markdown("**Cases by Issue Type**")
        if data["issue_counts"]:
            st.bar_chart(data["issue_counts"], height=260)
        else:
            st.info("No issue type data.")

    # Row 2: Week | Month
    col_w, col_m = st.columns(2)
    with col_w:
        st.markdown("**Cases by Week**")
        if data["week_counts"]:
            st.bar_chart(data["week_counts"], height=220)
        else:
            st.info("No weekly data.")

    with col_m:
        st.markdown("**Cases by Month**")
        if data["month_counts"]:
            st.bar_chart(data["month_counts"], height=220)
        else:
            st.info("No monthly data.")

    st.divider()

    # ── Voice of Customer ────────────────────────────────────────────────────────
    render_voc_analysis(cases)


# ── detail panels ──────────────────────────────────────────────────────────────

def render_email_thread(emails: list):
    st.subheader("📧 Email Thread")
    for i, email in enumerate(emails):
        with st.expander(
            f"**{email.get('from','').split('<')[0].strip()[:40]}** — "
            f"{email.get('subject','')[:55]}",
            expanded=(i == len(emails) - 1)
        ):
            col1, col2 = st.columns([2, 1])
            with col1:
                st.markdown(f"**From:** `{email.get('from','')}`")
                st.markdown(f"**To:** `{email.get('to','')}`")
                if email.get("cc"):
                    st.markdown(f"**CC:** `{email.get('cc','')}`")
            with col2:
                st.markdown(f"**Date:** {email.get('date','')}")
                labels = email.get("labels", [])
                for lbl in labels:
                    if lbl not in ("INBOX", "CATEGORY_FORUMS"):
                        st.markdown(
                            f'<span class="badge badge-status">{lbl}</span>',
                            unsafe_allow_html=True
                        )

            st.markdown("---")
            st.markdown(f"> {email.get('snippet','_(no preview)_')}")


def _extract_status_str(raw) -> str:
    """Return a plain string from a status field that may be a str or dict."""
    if isinstance(raw, dict):
        return (raw.get("status") or raw.get("trackingStatus") or
                raw.get("state") or "—")
    return str(raw) if raw else "—"


def _fmt_ts(ts) -> str:
    """Format a Unix-ms timestamp or ISO string to a readable date."""
    if not ts:
        return "—"
    if isinstance(ts, (int, float)):
        from datetime import datetime, timezone
        try:
            return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%d %b %Y")
        except Exception:
            return str(ts)
    return str(ts)[:10]


def _price_val(price) -> str:
    """Extract a numeric price from a plain number or a {'value':…} dict."""
    if isinstance(price, dict):
        return f"₹{price.get('value', 0):,.0f}"
    if isinstance(price, (int, float)):
        return f"₹{price:,.0f}"
    return "—"


def render_order_cards(vsm_orders: list):
    if not vsm_orders:
        st.info("No VSM order data available for this case.")
        return

    st.subheader("📦 VSM Order Details")
    for order in vsm_orders:
        st.markdown('<div class="order-card">', unsafe_allow_html=True)

        status_obj  = order.get("status") or {}
        created_str = _fmt_ts(order.get("created_at"))

        # Unpack status fields
        if isinstance(status_obj, dict):
            order_status   = status_obj.get("status") or "—"
            state          = status_obj.get("state") or "—"
            tracking_ckpt  = status_obj.get("orderTrackingStatusCheckpoint") or status_obj.get("trackingStatus") or "—"
            is_returnable  = status_obj.get("returnable", False)
            is_cancellable = status_obj.get("cancellable", False)
        else:
            order_status   = str(status_obj) if status_obj else "—"
            state = tracking_ckpt = "—"
            is_returnable = is_cancellable = False

        # ── Row 1: identity ────────────────────────────────────────────────
        col_id, col_status, col_state, col_ckpt = st.columns([2, 2, 1, 1])
        with col_id:
            st.markdown(f"### Order `{order.get('order_id')}`")
            name  = order.get("customer_name") or "—"
            phone = order.get("customer_phone") or "—"
            email = order.get("customer_email") or ""
            store = order.get("store") or "—"
            st.markdown(f"👤 **{name}** · 📱 `{phone}`")
            if email and not email.endswith("@lenskartomni.com"):
                st.markdown(f"✉️ `{email}`")
            st.markdown(f"🏬 {store} · 📅 {created_str}")

        with col_status:
            scolor = status_color(order_status)
            st.markdown(
                f"**Order Status**<br>"
                f"<span style='color:{scolor};font-size:13px;font-weight:700'>"
                f"{order_status.replace('_',' ')}</span>",
                unsafe_allow_html=True
            )
            # Cancellable / returnable flags
            flags = []
            if is_cancellable:
                flags.append("<span style='color:#ff4b4b;font-size:11px'>✖ Cancellable</span>")
            if is_returnable:
                flags.append("<span style='color:#4fc3f7;font-size:11px'>↩ Returnable</span>")
            if flags:
                st.markdown(" &nbsp; ".join(flags), unsafe_allow_html=True)

        with col_state:
            sc = "#888" if state == "CLOSED" else "#00c853"
            st.markdown(
                f"**State**<br><span style='color:{sc};font-weight:700'>{state}</span>",
                unsafe_allow_html=True
            )

        with col_ckpt:
            cc = status_color(tracking_ckpt)
            st.markdown(
                f"**Checkpoint**<br>"
                f"<span style='color:{cc};font-size:12px;font-weight:700'>"
                f"{tracking_ckpt.replace('_',' ')}</span>",
                unsafe_allow_html=True
            )

        # ── Row 2: amount + payment ────────────────────────────────────────
        amt = order.get("total_amount")
        payment_raw = order.get("payment_status")
        payment_str = _extract_status_str(payment_raw) if payment_raw else "—"
        if amt is not None or payment_str != "—":
            col_amt, col_pay, _ = st.columns([1, 1, 3])
            with col_amt:
                amt_str = f"₹{amt:,.0f}" if isinstance(amt, (int, float)) else "—"
                st.metric("Total Amount", amt_str)
            with col_pay:
                pcolor = payment_color(payment_str)
                st.markdown(
                    f"**Payment**<br>"
                    f"<span style='color:{pcolor};font-weight:700'>{payment_str}</span>",
                    unsafe_allow_html=True
                )

        # ── Tracking timeline ──────────────────────────────────────────────
        tracking_list = order.get("tracking") or []
        if isinstance(tracking_list, list) and tracking_list:
            st.markdown("**🚚 Tracking Timeline**")
            # Reverse so oldest → newest (left to right)
            for checkpoint in reversed(tracking_list):
                ck_status  = (checkpoint.get("status") or checkpoint.get("trackStatus") or "—")
                ck_time    = _fmt_ts(checkpoint.get("updatedTime") or checkpoint.get("createdTime"))
                details    = checkpoint.get("details") or []
                detail_labels = " · ".join(
                    d.get("time", "") + (f" ({d['trackStatus']})" if d.get("trackStatus") != ck_status else "")
                    for d in details
                ) if details else ""
                ck_color = status_color(ck_status)
                st.markdown(
                    f"<span style='color:{ck_color};font-weight:700'>{ck_status.replace('_',' ')}</span>"
                    f" <span style='color:#888;font-size:12px'>({ck_time})</span>"
                    + (f"<br><span style='color:#aaa;font-size:11px;margin-left:12px'>{detail_labels}</span>" if detail_labels else ""),
                    unsafe_allow_html=True
                )
        elif isinstance(tracking_list, dict):
            t_status = _extract_status_str(tracking_list)
            t_time   = _fmt_ts(tracking_list.get("updatedTime"))
            st.markdown(f"🚚 **{t_status}** · {t_time}")

        # ── Items ──────────────────────────────────────────────────────────
        items = order.get("items") or []
        if items:
            st.markdown("**Items:**")
            num_cols = min(len(items), 3)
            item_cols = st.columns(num_cols)
            for j, item in enumerate(items):
                with item_cols[j % num_cols]:
                    st.markdown(
                        f"<div style='background:#0f2040;border-radius:6px;padding:10px'>"
                        f"<b>{item.get('name','—')}</b><br>"
                        f"<small style='color:#aaa'>Qty: {item.get('qty', 1)}</small><br>"
                        f"<b style='color:#4fc3f7'>{_price_val(item.get('price', 0))}</b>"
                        f"</div>",
                        unsafe_allow_html=True
                    )

        if order.get("issue_type"):
            st.markdown(
                f"⚠️ **Reported Issue:** "
                f"<span style='color:#ff9800'>{order['issue_type']}</span>",
                unsafe_allow_html=True
            )

        st.markdown('</div>', unsafe_allow_html=True)


def render_crm_comments(crm_comments: dict):
    if not crm_comments:
        st.info("No CRM / WhatsApp data available.")
        return

    st.subheader("💬 CRM / WhatsApp Conversations")

    for order_id, comments in crm_comments.items():
        if not comments:
            continue

        with st.expander(f"Order {order_id} — {len(comments)} messages", expanded=True):
            html_parts = []
            for c in comments:
                direction = c.get("direction","outbound")
                msg = c.get("message","")
                author = c.get("author","")
                ts = str(c.get("created_at") or "")[:16]

                if direction == "inbound":
                    html_parts.append(
                        f'<div class="bubble-in">'
                        f'<span class="ts">👤 {author} · {ts}</span><br>{msg}'
                        f'</div><div class="clearfix"></div>'
                    )
                else:
                    html_parts.append(
                        f'<div class="bubble-out">'
                        f'<span class="ts">{ts} · {author} 🎧</span><br>{msg}'
                        f'</div><div class="clearfix"></div>'
                    )

            st.markdown("\n".join(html_parts), unsafe_allow_html=True)


def render_rca_panel(case: dict):
    st.subheader("🧠 Root Cause Analysis")

    rca_key = f"rca_{case['thread_id']}"

    # Check for existing RCA in session state
    existing_rca = st.session_state.get(rca_key)

    col1, col2 = st.columns([1, 5])
    with col1:
        api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
        gen_btn = st.button(
            "✨ Generate Initial RCA" if not existing_rca else "🔄 Regenerate",
            disabled=not api_key_set,
            type="primary",
            use_container_width=True,
        )
        if not api_key_set:
            st.caption("⚠️ Set `ANTHROPIC_API_KEY`")

    with col2:
        if existing_rca:
            st.caption("✅ RCA generated · scroll below")
        elif api_key_set:
            st.caption(
                "Stitches the complaint email with VSM order data · "
                "customer impact only · 6 crisp sections"
            )

    if gen_btn and api_key_set:
        # Import here to avoid loading anthropic before the user runs the app
        from rca_engine import generate_rca_streaming

        placeholder = st.empty()
        full_text = ""
        with st.spinner("Claude is drafting the initial RCA from the first escalation email…"):
            try:
                for chunk in generate_rca_streaming(case):
                    full_text += chunk
                    placeholder.markdown(full_text + "▌")
                    time.sleep(0)  # yield to Streamlit
                placeholder.markdown(full_text)
                st.session_state[rca_key] = full_text
            except Exception as e:
                st.error(f"RCA generation failed: {e}")
                st.stop()

    elif existing_rca:
        st.markdown(existing_rca)
    else:
        st.info(
            "RCA not yet generated. "
            "Click **Generate RCA** to have Claude Opus 4.6 perform deep analysis "
            "using the email thread, VSM order data, and CRM conversations."
        )

# ── main layout ────────────────────────────────────────────────────────────────

def render_case_header(case: dict):
    sev = case["severity"]
    sev_color = {"HIGH":"#ff4b4b","MEDIUM":"#ffa500","LOW":"#00c853"}.get(sev,"#888")

    col1, col2, col3, col4 = st.columns([4, 1, 1, 1])
    with col1:
        st.markdown(f"## {case['title']}")
        if case.get("case_number"):
            st.caption(f"Case # {case['case_number']} · Thread: `{case['thread_id'][:16]}…`")
    with col2:
        st.markdown(
            f"<div style='text-align:center;padding:8px;background:#1e1e2e;"
            f"border-radius:8px;border:2px solid {sev_color}'>"
            f"<div style='color:{sev_color};font-weight:700;font-size:18px'>{sev}</div>"
            f"<div style='color:#888;font-size:11px'>Severity</div></div>",
            unsafe_allow_html=True
        )
    with col3:
        st.markdown(
            f"<div style='text-align:center;padding:8px;background:#1e1e2e;"
            f"border-radius:8px;border:1px solid #333'>"
            f"<div style='font-weight:700;font-size:18px'>{case['email_count']}</div>"
            f"<div style='color:#888;font-size:11px'>Emails</div></div>",
            unsafe_allow_html=True
        )
    with col4:
        st.markdown(
            f"<div style='text-align:center;padding:8px;background:#1e1e2e;"
            f"border-radius:8px;border:1px solid #333'>"
            f"<div style='font-weight:700;font-size:18px'>{len(case['order_ids'])}</div>"
            f"<div style='color:#888;font-size:11px'>Orders</div></div>",
            unsafe_allow_html=True
        )

    st.markdown(
        f"**Issue Type:** "
        f"<span style='color:#ffa500;font-weight:600'>{case['issue_type']}</span>  |  "
        f"**Orders:** `{'`, `'.join(case['order_ids']) or 'None'}`  |  "
        f"**Last Activity:** {case['latest_date'][:16] if case['latest_date'] else '—'}",
        unsafe_allow_html=True
    )

    if case["participants"]:
        st.caption("👥 " + " · ".join(case["participants"][:5]))


def main():
    cases, enriched_at, meta = load_cases()

    if not cases:
        st.error("No data found. Run `python generate_mock_data.py` first.")
        st.stop()

    # Sidebar returns the filtered list
    filtered_cases = render_sidebar(cases, meta)

    # ── Top-level tabs ──────────────────────────────────────────────────────────
    tab_overview, tab_email, tab_order, tab_crm, tab_rca = st.tabs([
        "📊 Overview",
        "📧 Email Thread",
        "📦 Order Details",
        "💬 CRM / WhatsApp",
        "🧠 RCA Analysis",
    ])

    with tab_overview:
        render_overview_tab(cases, filtered_cases)

    # ── Case-detail tabs ────────────────────────────────────────────────────────
    if not filtered_cases:
        for tab in (tab_email, tab_order, tab_crm, tab_rca):
            with tab:
                st.warning("No cases match the current filters.")
    else:
        idx = st.session_state.get("selected_case_idx", 0)
        idx = min(idx, len(filtered_cases) - 1)
        case = filtered_cases[idx]

        for tab in (tab_email, tab_order, tab_crm, tab_rca):
            with tab:
                render_case_header(case)
                st.markdown("---")

        with tab_email:
            render_email_thread(case["emails"])

        with tab_order:
            render_order_cards(case["vsm_orders"])

        with tab_crm:
            render_crm_comments(case["crm_comments"])

        with tab_rca:
            render_rca_panel(case)

    # ── Footer ──────────────────────────────────────────────────────────────────
    st.markdown("---")
    cols = st.columns(4)
    cols[0].metric("Total Cases", len(cases))
    cols[1].metric("High Severity", sum(1 for c in cases if c["severity"] == "HIGH"))
    cols[2].metric("Unique Orders", sum(len(c["order_ids"]) for c in cases))
    cols[3].metric("RCA Generated",
                   sum(1 for k in st.session_state if k.startswith("rca_")))

    if enriched_at:
        st.caption(f"Data enriched at: {enriched_at}")

    # Auto-refresh: full sync every 5 minutes when enabled
    if st.session_state.get("auto_refresh"):
        time.sleep(300)
        _do_full_sync()
        st.rerun()


if __name__ == "__main__":
    main()
