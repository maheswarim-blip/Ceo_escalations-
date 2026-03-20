"""
RCA Engine - Uses Claude Opus 4.6 with adaptive thinking to generate
Root Cause Analysis for CEO escalation cases.

Supports streaming output for real-time display in the dashboard.
"""

import os
import anthropic
import httpx
from dotenv import load_dotenv

load_dotenv()


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


def _make_client() -> anthropic.Anthropic:
    """
    Build the Anthropic client, honouring SSL env vars.

    Set in .env (or shell) to work around SSL issues:
      REQUESTS_CA_BUNDLE=/path/to/corp-ca-bundle.pem   # custom CA cert
      SSL_CERT_FILE=/path/to/cert.pem                  # alternative CA path
      ANTHROPIC_DISABLE_SSL_VERIFY=true                # disable verification (dev only)
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # Resolve CA bundle: prefer explicit paths, then system defaults
    ca_bundle = (
        os.environ.get("REQUESTS_CA_BUNDLE")
        or os.environ.get("SSL_CERT_FILE")
    )
    disable_verify = os.environ.get("ANTHROPIC_DISABLE_SSL_VERIFY", "").lower() in (
        "1", "true", "yes"
    )

    if disable_verify:
        http_client = httpx.Client(verify=False)
    elif ca_bundle:
        http_client = httpx.Client(verify=ca_bundle)
    else:
        http_client = None  # default: let httpx use certifi / system certs

    kwargs = {"api_key": api_key}
    if http_client:
        kwargs["http_client"] = http_client

    return anthropic.Anthropic(**kwargs)


client = _make_client()

SYSTEM_PROMPT = """You are a senior Customer Experience analyst at Lenskart, India's leading eyewear company.
You specialize in performing Root Cause Analysis (RCA) on CEO-escalated customer complaints.

Your analysis must be structured, data-driven, and actionable. You have access to:
- Email thread content showing the escalation chain
- VSM (Visual Store Manager) order details including status, items, payments, tracking
- CRM WhatsApp/email conversation logs

For each case, provide a comprehensive RCA covering:
1. **Issue Summary** – What the customer complained about (1-2 sentences)
2. **Root Cause** – The primary reason this escalated (operational, process, human error, system)
3. **Contributing Factors** – Secondary issues that made the situation worse
4. **Timeline of Events** – Key events in chronological order
5. **Impact Assessment** – Customer impact + business impact
6. **Immediate Resolution** – What was or should be done NOW
7. **Preventive Actions** – Process/system changes to prevent recurrence
8. **Responsible Teams** – Which teams need to act (Store Ops, Logistics, Tech, Finance etc.)

Be specific. Reference order IDs, amounts, dates, and names where available.
Use bullet points for clarity. Flag any data inconsistencies you notice."""


def build_rca_context(case: dict) -> str:
    """Build a rich context string for the RCA prompt from a case dict."""
    parts = []

    # Email thread
    emails = case.get("emails", [])
    if emails:
        parts.append("## EMAIL THREAD\n")
        for e in emails:
            parts.append(f"**Date:** {e.get('date','')}")
            parts.append(f"**From:** {e.get('from','')}")
            parts.append(f"**Subject:** {e.get('subject','')}")
            parts.append(f"**Snippet:** {e.get('snippet','')}\n")

    # VSM order details (deduplicated by order_id)
    seen_orders = set()
    all_vsm = []
    for e in emails:
        for order in e.get("vsm_orders", []):
            oid = order.get("order_id")
            if oid and oid not in seen_orders:
                seen_orders.add(oid)
                all_vsm.append(order)

    if all_vsm:
        parts.append("## VSM ORDER DETAILS\n")
        for order in all_vsm:
            parts.append(f"**Order ID:** {order.get('order_id')}")

            # status is a nested dict in real VSM data
            status_raw = order.get("status")
            if isinstance(status_raw, dict):
                parts.append(f"**Order Status:** {status_raw.get('status')} | State: {status_raw.get('state')} | Checkpoint: {status_raw.get('orderTrackingStatusCheckpoint') or status_raw.get('trackingStatus')}")
                parts.append(f"**Returnable:** {status_raw.get('returnable')} | Cancellable: {status_raw.get('cancellable')}")
            else:
                parts.append(f"**Status:** {status_raw}")

            parts.append(f"**Customer:** {order.get('customer_name')} | {order.get('customer_phone')}")
            parts.append(f"**Store:** {order.get('store')}")
            parts.append(f"**Total Amount:** ₹{order.get('total_amount')}")

            pay_raw = order.get("payment_status")
            pay_str = pay_raw.get("status") if isinstance(pay_raw, dict) else pay_raw
            parts.append(f"**Payment Status:** {pay_str}")

            parts.append(f"**Created At:** {_fmt_ts(order.get('created_at'))}")

            # tracking is a list of checkpoints in real VSM data
            tracking_raw = order.get("tracking") or []
            if isinstance(tracking_raw, list) and tracking_raw:
                parts.append("**Tracking Timeline:**")
                for ck in reversed(tracking_raw):
                    ck_status = ck.get("status") or ck.get("trackStatus") or "—"
                    ck_time   = _fmt_ts(ck.get("updatedTime") or ck.get("createdTime"))
                    details   = " / ".join(
                        d.get("time", "") + (f" [{d['trackStatus']}]" if d.get("trackStatus") != ck_status else "")
                        for d in (ck.get("details") or [])
                    )
                    parts.append(f"  {ck_status} ({ck_time})" + (f": {details}" if details else ""))
            elif isinstance(tracking_raw, dict) and tracking_raw:
                parts.append(f"**Tracking:** {tracking_raw.get('status')} | Updated: {_fmt_ts(tracking_raw.get('updatedTime'))}")

            items = order.get("items") or []
            if items:
                parts.append("**Items:**")
                for item in items:
                    price_raw = item.get("price", 0)
                    price_str = f"₹{price_raw.get('value', 0):,.0f}" if isinstance(price_raw, dict) else f"₹{price_raw}"
                    parts.append(f"  - {item.get('name')} × {item.get('qty')} @ {price_str}")

            issue = order.get("issue_type")
            if issue:
                parts.append(f"**Reported Issue:** {issue}")

            parts.append("")

    # CRM comments (across all orders in this case)
    seen_comments: set = set()
    parts.append("## CRM / WHATSAPP CONVERSATION LOG\n")
    has_comments = False
    for e in emails:
        crm = e.get("crm_comments", {})
        for oid, comments in crm.items():
            for c in comments:
                cid = c.get("id", "")
                if cid in seen_comments:
                    continue
                seen_comments.add(cid)
                has_comments = True
                direction = c.get("direction", "")
                arrow = "→ Agent" if direction == "outbound" else "← Customer"
                ts = c.get("created_at", "")[:16]
                author = c.get("author", "Unknown")
                msg = c.get("message", "")
                parts.append(f"[{ts}] {arrow} ({author}): {msg}")

    if not has_comments:
        parts.append("_(No CRM conversation data available)_")

    return "\n".join(parts)


def generate_rca_streaming(case: dict):
    """
    Generator that streams RCA text tokens for a given case dict.
    Each yield is a string chunk.
    Caller assembles the full RCA.
    """
    context = build_rca_context(case)
    order_ids = list({
        o.get("order_id")
        for e in case.get("emails", [])
        for o in e.get("vsm_orders", [])
        if o.get("order_id")
    })

    prompt = f"""Please perform a detailed Root Cause Analysis for this CEO escalation case.

**Case Title:** {case.get('title', 'CEO Escalation')}
**Order IDs:** {', '.join(str(oid) for oid in order_ids) if order_ids else 'N/A'}
**Total Emails in Thread:** {len(case.get('emails', []))}

---

{context}

---

Generate the complete RCA now."""

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=4096,
        thinking={"type": "enabled", "budget_tokens": 2000},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for event in stream:
            if event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    yield event.delta.text
        # Yield usage stats at end as a special marker
        final = stream.get_final_message()
        usage = final.usage
        yield f"\n\n---\n_Tokens — Input: {usage.input_tokens} | Output: {usage.output_tokens}_"


def generate_rca_sync(case: dict) -> str:
    """
    Non-streaming version - returns complete RCA string.
    Useful for batch processing.
    """
    return "".join(generate_rca_streaming(case))


# ── standalone test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    from pathlib import Path
    from case_builder import build_cases

    data_path = Path("ceo_escalation_emails_enriched.json")
    if not data_path.exists():
        print("[!] Run generate_mock_data.py first")
        exit(1)

    with open(data_path) as f:
        data = json.load(f)

    cases = build_cases(data["emails"])
    if not cases:
        print("[!] No cases found")
        exit(1)

    # Test RCA on the first case
    case = cases[0]
    print(f"\n{'='*60}")
    print(f"RCA for: {case['title']}")
    print(f"{'='*60}\n")

    for chunk in generate_rca_streaming(case):
        print(chunk, end="", flush=True)
    print()
