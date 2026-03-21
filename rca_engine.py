"""
RCA Engine - Uses Claude Opus 4.6 with adaptive thinking to generate
an initial draft Root Cause Analysis for CEO escalation cases.

The RCA is based ONLY on the first (original) escalation email and
its matched VSM order data.  Replies in the same thread are intentionally
excluded so the analysis reflects what is known at the moment of escalation.
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
        http_client = None

    kwargs = {"api_key": api_key}
    if http_client:
        kwargs["http_client"] = http_client

    return anthropic.Anthropic(**kwargs)


client = _make_client()

SYSTEM_PROMPT = """You are a senior Customer Experience analyst at Lenskart, India's leading eyewear company.

Your task is to produce an **Initial Draft RCA** for a CEO-escalated complaint based solely on the
FIRST escalation email received at ceoescalation@lenskart.com and the VSM order details matched
to that email. No follow-up replies are available yet.

Your output must contain exactly these sections:

---

## 1. Escalation Summary
One paragraph covering: who escalated, on behalf of which customer, the order ID(s) involved,
the date the order was placed, and the nature of the complaint as stated in the email.

## 2. Initial Root Cause (Hypothesis)
Based only on available data, state the most likely root cause.
Clearly label it as a hypothesis until confirmed.
Categories: Operational Delay | Process Failure | Human Error | System / Tech Issue | Logistics | Product Quality | Communication Gap

## 3. VSM Order Snapshot
Summarise what the VSM data shows: order status, payment status, tracking checkpoint, items ordered, order value.
Highlight anything that corroborates or contradicts the complaint.

## 4. Timeline of Known Events
Chronological list of events derived from the email date, order creation date, and tracking checkpoints.
Mark any unexplained gaps.

## 5. Customer & Business Impact
- Customer impact (financial, experience, trust)
- Business impact (brand risk given CEO escalation, potential refund/re-order cost)

## 6. Immediate Recommended Action
What should be done in the next 24 hours? Be specific about team and action.

## 7. Information Gaps — What Is Needed to Close This RCA
This section is critical. List every piece of information that is MISSING and would change or confirm the root cause.
For each gap, state:
  • What data is missing
  • Where / from which team it should be obtained
  • How it would impact the RCA conclusion

## 8. Responsible Teams
List teams that need to investigate or act, with the specific ask for each.

## 9. RCA Confidence Score
Rate your confidence in the current RCA: **Low / Medium / High**
Explain why, referencing the information gaps above.

---

Be specific. Reference order IDs, amounts, dates, and names wherever available.
Do NOT fabricate data. If a field is blank or unknown, state it explicitly rather than guessing.
Use bullet points for clarity."""


def get_first_email(case: dict) -> dict | None:
    """
    Return the first (original) escalation email in the thread.
    Emails are already sorted by date ascending in case_builder.build_cases().
    """
    emails = case.get("emails", [])
    return emails[0] if emails else None


def build_rca_context(case: dict) -> str:
    """
    Build the context string for the RCA prompt using ONLY the first email
    and its matched VSM order data.
    """
    parts = []

    first_email = get_first_email(case)
    if not first_email:
        return "No email data available."

    # ── First (original) escalation email ────────────────────────────────────
    parts.append("## ORIGINAL ESCALATION EMAIL\n")
    parts.append(f"**Date Received:** {first_email.get('date', '—')}")
    parts.append(f"**From:** {first_email.get('from', '—')}")
    parts.append(f"**To:** {first_email.get('to', '—')}")
    if first_email.get("cc"):
        parts.append(f"**CC:** {first_email.get('cc', '')}")
    parts.append(f"**Subject:** {first_email.get('subject', '—')}")
    parts.append(f"**Email Content (snippet):**\n{first_email.get('snippet', '_(no preview available)_')}\n")

    # ── VSM orders linked to the first email ─────────────────────────────────
    vsm_orders = first_email.get("vsm_orders", [])
    if vsm_orders:
        parts.append("## VSM ORDER DATA (matched from first email)\n")
        for order in vsm_orders:
            parts.append(f"**Order ID:** {order.get('order_id')}")

            status_raw = order.get("status")
            if isinstance(status_raw, dict):
                parts.append(
                    f"**Order Status:** {status_raw.get('status')} "
                    f"| State: {status_raw.get('state')} "
                    f"| Checkpoint: {status_raw.get('orderTrackingStatusCheckpoint') or status_raw.get('trackingStatus')}"
                )
                parts.append(
                    f"**Returnable:** {status_raw.get('returnable')} "
                    f"| Cancellable: {status_raw.get('cancellable')}"
                )
            else:
                parts.append(f"**Status:** {status_raw}")

            customer_name = order.get("customer_name") or "— (not available in VSM)"
            parts.append(f"**Customer Name:** {customer_name}")
            parts.append(f"**Customer Phone:** {order.get('customer_phone') or '—'}")
            parts.append(f"**Customer Email:** {order.get('customer_email') or '—'}")
            parts.append(f"**Store:** {order.get('store') or '—'}")

            amt = order.get("total_amount")
            parts.append(f"**Order Value:** ₹{amt:,.0f}" if isinstance(amt, (int, float)) else "**Order Value:** —")
            parts.append(f"**Order Date:** {_fmt_ts(order.get('created_at'))}")

            pay_raw = order.get("payment_status")
            pay_str = pay_raw.get("status") if isinstance(pay_raw, dict) else (pay_raw or "—")
            parts.append(f"**Payment Status:** {pay_str}")

            # Tracking timeline
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
                    parts.append(
                        f"  {ck_status} ({ck_time})"
                        + (f": {details}" if details else "")
                    )
            elif isinstance(tracking_raw, dict) and tracking_raw:
                parts.append(
                    f"**Tracking:** {tracking_raw.get('status')} "
                    f"| Updated: {_fmt_ts(tracking_raw.get('updatedTime'))}"
                )

            items = order.get("items") or []
            if items:
                parts.append("**Items Ordered:**")
                for item in items:
                    price_raw = item.get("price", 0)
                    price_str = (
                        f"₹{price_raw.get('value', 0):,.0f}"
                        if isinstance(price_raw, dict)
                        else f"₹{price_raw}"
                    )
                    parts.append(f"  - {item.get('name')} × {item.get('qty')} @ {price_str}")

            parts.append("")
    else:
        parts.append("## VSM ORDER DATA\n_(No VSM order data was matched for this email)_\n")

    # ── CRM data for orders in the first email ────────────────────────────────
    first_email_order_ids = {
        str(o.get("order_id")) for o in vsm_orders if o.get("order_id")
    }
    crm_all = first_email.get("crm_comments", {})
    relevant_crm = {
        oid: comments
        for oid, comments in crm_all.items()
        if str(oid) in first_email_order_ids
    }

    if relevant_crm:
        parts.append("## CRM / WHATSAPP LOG (for matched orders)\n")
        for oid, comments in relevant_crm.items():
            parts.append(f"**Order {oid}:**")
            for c in comments:
                direction = c.get("direction", "")
                arrow = "→ Agent" if direction == "outbound" else "← Customer"
                ts = c.get("created_at", "")[:16]
                author = c.get("author", "Unknown")
                msg = c.get("message", "")
                parts.append(f"  [{ts}] {arrow} ({author}): {msg}")
        parts.append("")
    else:
        parts.append("## CRM / WHATSAPP LOG\n_(No CRM data available for this order)_\n")

    # ── Thread context note ───────────────────────────────────────────────────
    total_emails = len(case.get("emails", []))
    if total_emails > 1:
        parts.append(
            f"## NOTE\n"
            f"This thread has {total_emails} emails in total. "
            f"The RCA above is based ONLY on the first email. "
            f"The {total_emails - 1} reply email(s) have been intentionally excluded "
            f"to produce an initial draft RCA at the point of escalation.\n"
        )

    return "\n".join(parts)


def generate_rca_streaming(case: dict):
    """
    Generator that streams RCA text tokens for a given case dict.
    Uses only the first email for analysis.
    Each yield is a string chunk. Caller assembles the full RCA.
    """
    first_email = get_first_email(case)
    if not first_email:
        yield "No email data found for this case."
        return

    context = build_rca_context(case)

    # Collect order IDs from the first email only
    order_ids = [
        str(o.get("order_id"))
        for o in first_email.get("vsm_orders", [])
        if o.get("order_id")
    ]

    # Try to extract customer name from VSM or email snippet
    customer_name = next(
        (o.get("customer_name") for o in first_email.get("vsm_orders", []) if o.get("customer_name")),
        None,
    ) or "— (not available)"

    order_dates = [
        _fmt_ts(o.get("created_at"))
        for o in first_email.get("vsm_orders", [])
        if o.get("created_at")
    ]

    prompt = f"""Please produce an Initial Draft RCA for this CEO escalation.

**Case Title:** {case.get('title', 'CEO Escalation')}
**Escalation Email Date:** {first_email.get('date', '—')}
**Order ID(s):** {', '.join(order_ids) if order_ids else 'Not extracted'}
**Customer Name (from VSM):** {customer_name}
**Order Date(s):** {', '.join(order_dates) if order_dates else '—'}

This analysis is based on the FIRST email in the escalation thread only.
Do not assume information from any follow-up replies.

---

{context}

---

Generate the Initial Draft RCA now, following the required section structure exactly."""

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
        final = stream.get_final_message()
        usage = final.usage
        yield (
            f"\n\n---\n"
            f"_Initial draft RCA · Based on first escalation email only · "
            f"Tokens — Input: {usage.input_tokens} | Output: {usage.output_tokens}_"
        )


def generate_rca_sync(case: dict) -> str:
    """Non-streaming version - returns complete RCA string."""
    return "".join(generate_rca_streaming(case))


# ── standalone test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    from pathlib import Path
    from case_builder import build_cases

    data_path = Path("ceo_escalation_emails_enriched.json")
    if not data_path.exists():
        print("[!] Run gmail_sync.py first")
        exit(1)

    with open(data_path) as f:
        data = json.load(f)

    cases = build_cases(data["emails"])
    if not cases:
        print("[!] No cases found")
        exit(1)

    case = cases[0]
    first = case["emails"][0]
    print(f"\n{'='*60}")
    print(f"Initial Draft RCA for: {case['title']}")
    print(f"Based on first email from: {first.get('from','')[:60]}")
    print(f"Email date: {first.get('date','')}")
    print(f"{'='*60}\n")

    for chunk in generate_rca_streaming(case):
        print(chunk, end="", flush=True)
    print()
