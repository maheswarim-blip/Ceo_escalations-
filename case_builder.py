"""
Groups individual emails into logical cases (by thread ID + subject).
A case = one escalation thread with all its emails, orders, and CRM data.
"""

import re
from collections import defaultdict
from email.utils import parsedate_to_datetime


def extract_case_number(text: str) -> str | None:
    """Extract Salesforce/CRM case number (6-9 digit number starting with 1)."""
    # Case numbers like 118784423, 116408181
    matches = re.findall(r"\b1[0-9]{8}\b", text)
    # Filter out order IDs (10 digits)
    return matches[0] if matches else None


def extract_order_ids(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\b1[0-9]{9}\b", text)))


def infer_issue_type(subject: str, snippets: list[str]) -> str:
    """
    Map escalation text to one of five business themes:
      Product Quality | Delivery Timelines | Staff Behaviour |
      Pricing & Offers | Customer Expectations
    """
    text = (subject + " " + " ".join(snippets)).lower()

    # ── Product Quality ────────────────────────────────────────────────────────
    # Lens/frame defects, prescription errors, power mismatch, wrong item
    if any(kw in text for kw in (
        "damage", "damaged", "defect", "defective", "broken", "crack", "scratch",
        "prescription", "power mismatch", "wrong power", "wrong lens", "lens issue",
        "wrong product", "wrong item", "mismatch", "wrong frame",
        "quality", "tint", "coating", "faulty",
    )):
        return "Product Quality"

    # ── Delivery Timelines ─────────────────────────────────────────────────────
    # Delays, non-delivery, partial, dispatch issues
    if any(kw in text for kw in (
        "delay", "delayed", "not delivered", "not received", "not dispatch",
        "undelivered", "pending delivery", "awaiting dispatch", "shipment",
        "partial delivery", "partial", "missing item", "not shipped",
        "logistics", "courier", "tracking",
    )):
        return "Delivery Timelines"

    # ── Staff Behaviour ────────────────────────────────────────────────────────
    # In-store experience, wrong advice, rude/unprofessional staff
    if any(kw in text for kw in (
        "staff", "rude", "behaviour", "behavior", "wrong practice", "misinformation",
        "mislead", "unprofessional", "in-store", "store team", "executive",
        "optometrist", "eye test", "wrong advice", "hygiene", "infrastructure",
        "ambiguity", "invoice discrepancy", "warehouse",
    )):
        return "Staff Behaviour"

    # ── Pricing & Offers ───────────────────────────────────────────────────────
    # Refunds, billing, discounts, EMI, payment issues
    if any(kw in text for kw in (
        "refund", "billing", "invoice", "price", "pricing", "offer", "discount",
        "cashback", "emi", "payment", "charge", "overcharge", "fee",
        "amount", "cost", "money", "paid",
    )):
        return "Pricing & Offers"

    # ── Customer Expectations ──────────────────────────────────────────────────
    # Social media, unmet experience, general dissatisfaction, HTO, exchange
    if any(kw in text for kw in (
        "social media", "instagram", "linkedin", "twitter", "facebook",
        "experience", "expectation", "dissatisfied", "unsatisfied", "unhappy",
        "disappointed", "complaint", "feedback", "escalat",
        "exchange", "return", "hto", "home trial", "visit",
    )):
        return "Customer Expectations"

    return "Customer Expectations"  # safe default — any escalation is an unmet expectation


def infer_severity(labels: list[list[str]]) -> str:
    all_labels = [l for group in labels for l in group]
    if "IMPORTANT" in all_labels:
        return "HIGH"
    if "UNREAD" in all_labels:
        return "MEDIUM"
    return "LOW"


def build_cases(emails: list[dict]) -> list[dict]:
    """
    Group emails by threadId. Each group becomes one case.
    Returns list of case dicts sorted by most recent email first.
    """
    threads: dict[str, list[dict]] = defaultdict(list)
    for email in emails:
        tid = email.get("threadId") or email.get("id", "unknown")
        threads[tid].append(email)

    cases = []
    for thread_id, thread_emails in threads.items():
        # Sort emails by date within thread (parse RFC 2822 dates properly)
        def _parse_date(e):
            try:
                return parsedate_to_datetime(e.get("date", ""))
            except Exception:
                return e.get("date", "")

        thread_emails.sort(key=_parse_date)

        # Use first email's subject as case title (strip Re:/Fwd: prefixes)
        raw_subject = thread_emails[0].get("subject", "Unknown Subject")
        title = re.sub(r"^(Re:|Fwd:|FW:)\s*", "", raw_subject, flags=re.IGNORECASE).strip()

        # Collect all order IDs across thread
        all_order_ids: list[str] = []
        for e in thread_emails:
            all_order_ids.extend(e.get("order_ids") or
                                 extract_order_ids(f"{e.get('subject','')} {e.get('snippet','')}"))
        all_order_ids = list(dict.fromkeys(all_order_ids))

        # Extract case number
        combined_text = " ".join(e.get("subject","") + " " + e.get("snippet","")
                                 for e in thread_emails)
        case_number = extract_case_number(combined_text)

        # Collect all VSM orders (deduplicated)
        vsm_map: dict[str, dict] = {}
        crm_map: dict[str, list] = {}
        for e in thread_emails:
            for order in e.get("vsm_orders", []):
                oid = order.get("order_id")
                if oid:
                    vsm_map[oid] = order
            for oid, comments in (e.get("crm_comments") or {}).items():
                if oid not in crm_map:
                    crm_map[oid] = comments

        # Determine latest date
        latest_date = max((e.get("date","") for e in thread_emails), default="")

        # Collect unique participants
        participants = list(dict.fromkeys(
            e.get("from","").split("<")[0].strip().strip("'\"")
            for e in thread_emails if e.get("from")
        ))

        issue_type = infer_issue_type(
            raw_subject,
            [e.get("snippet","") for e in thread_emails]
        )

        severity = infer_severity([e.get("labels",[]) for e in thread_emails])

        cases.append({
            "thread_id":   thread_id,
            "title":       title,
            "case_number": case_number,
            "order_ids":   all_order_ids,
            "issue_type":  issue_type,
            "severity":    severity,
            "latest_date": latest_date,
            "email_count": len(thread_emails),
            "participants": participants,
            "emails":      thread_emails,
            "vsm_orders":  list(vsm_map.values()),
            "crm_comments": crm_map,
            "rca":         None,   # filled in by rca_engine
        })

    # Sort by latest email date descending
    cases.sort(key=lambda c: c["latest_date"], reverse=True)
    return cases
