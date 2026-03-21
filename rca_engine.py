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

SYSTEM_PROMPT = """You are a Customer Experience analyst at Lenskart.
You write crisp, action-oriented RCAs for CEO-escalated complaints.

Audience: the Customer Experience team. They need to understand exactly what went wrong
for the customer and act immediately. Do NOT include business impact, brand risk, revenue
exposure, or refund cost — those are irrelevant here. Focus entirely on the customer's experience.

Output exactly these six sections. Keep every section short and punchy — bullets preferred,
no long paragraphs.

---

## What Happened
2–3 sentences max. Cover: customer name, order ID(s), complaint as described in the email,
order date, and date the complaint was raised.

## Root Cause
1–2 sentences. State the most likely root cause and label it as confirmed or hypothesis.
Pick one category: `Operational Delay | Process Failure | Human Error | System Issue | Logistics | Product Quality | Communication Gap`
Then explain in one line exactly where in the customer journey the failure occurred — connect
what the customer says in the email to what the VSM data shows (or doesn't show).

## What the Customer Experienced
Bullet list of the customer journey from order placement to escalation. Use dates wherever
available. Highlight each moment where the experience broke down: delays, missed SLAs,
wrong product, no communication, repeated failures. This should read as the customer's story,
not an internal report.

## Order Status at Time of Escalation
Bullet list from VSM:
- Order status and last tracking checkpoint
- Payment status
- Items ordered and order value
- Call out anything that directly explains or contradicts what the customer reported

## What Must Happen in 24 Hours
Numbered list. For each action specify: team responsible + exact action + expected outcome for the customer.

## What Is Still Unknown
Bullet list of missing data that would change the root cause conclusion, and which team owns each gap.
If nothing is missing, write "Sufficient data available to close RCA."

---

Rules:
- Always quote order IDs, amounts, dates, and team names.
- Never fabricate. If a field is blank write "not available".
- No business impact. No brand risk. No revenue figures.
- Bullets and short sentences only. No padding."""


import re as _re

def _extract_phones(text: str) -> list[str]:
    """Extract 10-digit Indian mobile numbers from text."""
    return list(dict.fromkeys(_re.findall(r"\b[6-9]\d{9}\b", text)))


def _extract_forwarded_customer_email(body: str) -> dict | None:
    """
    Parse a forwarded email body to extract the original customer message.
    Looks for '---------- Forwarded message ---------' blocks and returns
    the innermost one that originated from an external (non-lenskart) sender.

    Returns a dict with keys: from_, date, subject, body
    Returns None if no forwarded customer email is found.
    """
    # Split on forwarded-message dividers (handle nested forwards)
    # Pattern used by Gmail: ---------- Forwarded message ---------
    parts = _re.split(
        r"-{5,}\s*Forwarded message\s*-{5,}",
        body,
        flags=_re.IGNORECASE
    )

    for part in reversed(parts):  # innermost forward first
        lines = part.strip().splitlines()
        meta = {}
        body_lines = []
        in_meta = True
        for line in lines:
            if in_meta:
                m = _re.match(r"^(From|Date|Subject|To|Cc):\s*(.+)$", line, _re.IGNORECASE)
                if m:
                    meta[m.group(1).lower()] = m.group(2).strip()
                    continue
                elif meta:  # meta is done, rest is body
                    in_meta = False
            body_lines.append(line)

        sender = meta.get("from", "")
        # Only consider external senders (not @lenskart.com / @valyoo.in domains)
        if sender and not _re.search(r"@(lenskart\.com|lenskart\.in|valyoo\.in|lenskart\.mobi)", sender, _re.IGNORECASE):
            extracted_body = "\n".join(body_lines).strip()
            if len(extracted_body) > 50:  # ignore near-empty blocks
                return {
                    "from_": sender,
                    "date": meta.get("date", ""),
                    "subject": meta.get("subject", ""),
                    "body": extracted_body,
                }
    return None


def get_first_email(case: dict) -> dict | None:
    """
    Return the first (original) escalation email in the thread.
    Emails are sorted by date ascending in case_builder.build_cases().
    """
    emails = case.get("emails", [])
    return emails[0] if emails else None


def _get_vsm_orders_for_rca(case: dict, first_email: dict) -> list[dict]:
    """
    Return VSM orders to use in the RCA.
    Preference: orders attached to the first email.
    Fallback: case-level aggregated VSM orders (deduped across all thread emails).
    This handles the common pattern where the forwarding email has no order ID
    in its snippet, but a later reply's VSM data was fetched successfully.
    """
    first_email_orders = first_email.get("vsm_orders", [])
    if first_email_orders:
        return first_email_orders
    # Fall back to case-level VSM orders (same case, same complaint)
    case_orders = case.get("vsm_orders", [])
    return case_orders


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
    # Prefer full body (stored by gmail_sync), then body_preview, then snippet
    full_body = first_email.get("body") or first_email.get("body_preview") or ""

    # Try to extract the original customer complaint embedded in a forwarded chain
    customer_email = _extract_forwarded_customer_email(full_body) if full_body else None

    if customer_email:
        parts.append("## ORIGINAL CUSTOMER EMAIL (extracted from forwarded chain)\n")
        parts.append(f"**Customer:** {customer_email['from_']}")
        parts.append(f"**Sent:** {customer_email['date']}")
        parts.append(f"**Subject:** {customer_email['subject']}")
        parts.append(f"**Message:**\n{customer_email['body']}\n")

        parts.append("## ESCALATION CHAIN\n")
        parts.append(f"**Forwarded by:** {first_email.get('from', '—')}")
        parts.append(f"**Date forwarded to CEO escalation:** {first_email.get('date', '—')}")
        if full_body:
            # Show the escalating manager's own note (text before the first forward divider)
            manager_note = full_body.split("---------- Forwarded message")[0].strip()
            if manager_note and len(manager_note) > 5:
                parts.append(f"**Escalation note:** {manager_note}\n")
    else:
        parts.append("## ORIGINAL ESCALATION EMAIL\n")
        parts.append(f"**Date Received:** {first_email.get('date', '—')}")
        parts.append(f"**From:** {first_email.get('from', '—')}")
        parts.append(f"**To:** {first_email.get('to', '—')}")
        if first_email.get("cc"):
            parts.append(f"**CC:** {first_email.get('cc', '')}")
        parts.append(f"**Subject:** {first_email.get('subject', '—')}")
        email_body = full_body or first_email.get("snippet") or "_(no preview available)_"
        parts.append(f"**Email Content:**\n{email_body}\n")

    # ── VSM orders: first-email preferred, case-level fallback ───────────────
    vsm_orders = _get_vsm_orders_for_rca(case, first_email)
    vsm_source = "first email" if first_email.get("vsm_orders") else "thread (order found in reply)"

    # Phone numbers extracted from the email content (customer often includes theirs)
    snippet_phones = _extract_phones(first_email.get("snippet", ""))
    if snippet_phones:
        parts.append(f"**Customer Phone (from email body):** {', '.join(snippet_phones)}\n")
    if vsm_orders:
        parts.append(f"## VSM ORDER DATA (source: {vsm_source})\n")
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
    vsm_orders = _get_vsm_orders_for_rca(case, first_email)

    # Collect order IDs from VSM (first-email preferred, case-level fallback)
    order_ids = [str(o.get("order_id")) for o in vsm_orders if o.get("order_id")]

    # Customer name: VSM first, then from the extracted customer email sender
    customer_name = next(
        (o.get("customer_name") for o in vsm_orders if o.get("customer_name")),
        None,
    )
    if not customer_name:
        full_body = first_email.get("body") or first_email.get("body_preview") or ""
        cust_email = _extract_forwarded_customer_email(full_body) if full_body else None
        if cust_email:
            # Extract name from "Name <email>" format
            customer_name = cust_email["from_"].split("<")[0].strip().strip("'\"") or "— (not available)"
        else:
            customer_name = "— (not available)"

    order_dates = [
        _fmt_ts(o.get("created_at"))
        for o in first_email.get("vsm_orders", [])
        if o.get("created_at")
    ]

    has_vsm = bool(vsm_orders and any(not o.get("error") for o in vsm_orders))
    vsm_note = (
        "VSM order data is available — cross-reference the complaint text with the order status and tracking."
        if has_vsm
        else "VSM order data could not be fetched — base the analysis on the complaint text alone and flag this gap."
    )

    prompt = f"""Produce an Initial Draft RCA for this CEO escalation.

**Case:** {case.get('title', 'CEO Escalation')}
**Escalation date:** {first_email.get('date', '—')}
**Order ID(s):** {', '.join(order_ids) if order_ids else 'not extracted from email'}
**Customer:** {customer_name}
**Order date(s):** {', '.join(order_dates) if order_dates else '—'}

{vsm_note}

---

{context}

---

Write the RCA now. Stitch together what the customer says in their complaint with what the
VSM order data shows — point to specific facts from both sources in every section.
Follow the six-section structure exactly. Be crisp."""

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=5000,
        thinking={"type": "enabled", "budget_tokens": 3000},
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
            f"_Draft RCA · Based on escalation email + VSM data · "
            f"Input: {usage.input_tokens} tokens_"
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
