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
import time
from pathlib import Path

import streamlit as st

from case_builder import build_cases

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


def _gmail_sync_available() -> bool:
    token_file = os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE", "")
    return bool(token_file and os.path.exists(token_file))

# ── sidebar ────────────────────────────────────────────────────────────────────

def severity_icon(s):
    return {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟢"}.get(s, "⚪")

def _do_sync():
    """Run Gmail sync and clear the cache so the dashboard reloads."""
    try:
        from gmail_sync import sync_new_emails
        result = sync_new_emails()
        st.session_state["last_sync_result"] = result
        load_cases.clear()
    except Exception as e:
        st.session_state["last_sync_result"] = {"error": str(e)}


def render_sidebar(cases, meta=None):
    with st.sidebar:
        st.title("🔺 CEO Escalations")
        st.caption(f"{len(cases)} active cases")

        # ── Real-time sync controls ──────────────────────────────────────────
        st.divider()
        sync_available = _gmail_sync_available()
        col_btn, col_auto = st.columns([3, 2])

        with col_btn:
            if st.button(
                "🔄 Sync Gmail",
                disabled=not sync_available,
                use_container_width=True,
                help="Fetch new emails from ceoescalation@lenskart.com" if sync_available
                     else "Available only inside a Claude Code session",
            ):
                with st.spinner("Syncing…"):
                    _do_sync()
                st.rerun()

        with col_auto:
            auto = st.toggle("Auto", value=st.session_state.get("auto_refresh", False),
                             help="Auto-refresh every 2 min")
            st.session_state["auto_refresh"] = auto

        # Show last sync result
        lsr = st.session_state.get("last_sync_result")
        if lsr:
            if "error" in lsr:
                st.error(f"Sync failed: {lsr['error']}", icon="⚠️")
            else:
                st.success(
                    f"+{lsr['new_count']} new · {lsr['total']} total",
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
    if s in ("DELIVERED",):
        return "#00c853"
    if s in ("DISPATCHED", "PROCESSING"):
        return "#ffa500"
    if s in ("CANCELLED", "RETURNED"):
        return "#ff4b4b"
    return "#888"


def payment_color(status: str) -> str:
    s = (status or "").upper()
    if "REFUND" in s:
        return "#ff9800"
    if s == "PAID":
        return "#00c853"
    return "#888"

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


def render_order_cards(vsm_orders: list):
    if not vsm_orders:
        st.info("No VSM order data available for this case.")
        return

    st.subheader("📦 VSM Order Details")
    for order in vsm_orders:
        st.markdown('<div class="order-card">', unsafe_allow_html=True)

        cols = st.columns([2, 1, 1, 1])
        with cols[0]:
            st.markdown(f"### Order `{order.get('order_id')}`")
            st.markdown(f"👤 **{order.get('customer_name')}** · 📱 `{order.get('customer_phone')}`")
            st.markdown(f"🏬 {order.get('store','—')} · 📅 {(order.get('created_at') or '')[:10]}")

        with cols[1]:
            scolor = status_color(order.get("status",""))
            st.markdown(
                f"**Order Status**<br>"
                f"<span style='color:{scolor};font-size:16px;font-weight:700'>"
                f"{order.get('status','—')}</span>",
                unsafe_allow_html=True
            )

        with cols[2]:
            pcolor = payment_color(order.get("payment_status",""))
            st.markdown(
                f"**Payment**<br>"
                f"<span style='color:{pcolor};font-size:16px;font-weight:700'>"
                f"{order.get('payment_status','—')}</span>",
                unsafe_allow_html=True
            )

        with cols[3]:
            st.metric("Total Amount", f"₹{order.get('total_amount','—'):,}" if isinstance(order.get('total_amount'), int) else "—")

        # Tracking
        tracking = order.get("tracking") or {}
        if tracking:
            st.markdown(
                f"🚚 **{tracking.get('courier','—')}** · AWB: `{tracking.get('awb','—')}` · "
                f"Status: **{tracking.get('status','—')}** · "
                f"Updated: {(tracking.get('updated_at') or '')[:10]}"
            )

        # Items
        items = order.get("items", [])
        if items:
            st.markdown("**Items:**")
            item_cols = st.columns(len(items))
            for j, item in enumerate(items):
                with item_cols[j]:
                    st.markdown(
                        f"<div style='background:#0f2040;border-radius:6px;padding:10px'>"
                        f"<b>{item.get('name','—')}</b><br>"
                        f"<small>SKU: {item.get('sku','—')} · Qty: {item.get('qty',1)}</small><br>"
                        f"<b>₹{item.get('price',0):,}</b>"
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
                ts = (c.get("created_at") or "")[:16]

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
            "✨ Generate RCA" if not existing_rca else "🔄 Regenerate",
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
            st.caption("Click to generate AI-powered RCA using Claude Opus 4.6")

    if gen_btn and api_key_set:
        # Import here to avoid loading anthropic before the user runs the app
        from rca_engine import generate_rca_streaming

        placeholder = st.empty()
        full_text = ""
        with st.spinner("Claude is analyzing the case with adaptive thinking..."):
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

    if not filtered_cases:
        st.warning("No cases match the current filters.")
        st.stop()

    # Get selected case
    idx = st.session_state.get("selected_case_idx", 0)
    idx = min(idx, len(filtered_cases) - 1)
    case = filtered_cases[idx]

    # Header metrics
    render_case_header(case)

    st.markdown("---")

    # Main tab layout
    tab_email, tab_order, tab_crm, tab_rca = st.tabs([
        "📧 Email Thread",
        "📦 Order Details",
        "💬 CRM / WhatsApp",
        "🧠 RCA Analysis",
    ])

    with tab_email:
        render_email_thread(case["emails"])

    with tab_order:
        render_order_cards(case["vsm_orders"])

    with tab_crm:
        render_crm_comments(case["crm_comments"])

    with tab_rca:
        render_rca_panel(case)

    # Footer
    st.markdown("---")
    cols = st.columns(4)
    cols[0].metric("Total Cases", len(cases))
    cols[1].metric("High Severity", sum(1 for c in cases if c["severity"] == "HIGH"))
    cols[2].metric("Unique Orders", sum(len(c["order_ids"]) for c in cases))
    cols[3].metric("RCA Generated",
                   sum(1 for k in st.session_state if k.startswith("rca_")))

    if enriched_at:
        st.caption(f"Data enriched at: {enriched_at}")

    # Auto-refresh: rerun every 2 minutes when enabled
    if st.session_state.get("auto_refresh"):
        time.sleep(120)
        load_cases.clear()
        st.rerun()


if __name__ == "__main__":
    main()
