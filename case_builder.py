"""
Groups individual emails into logical cases (by thread ID + subject).
A case = one escalation thread with all its emails, orders, and CRM data.
"""

import re
from collections import defaultdict


def extract_case_number(text: str) -> str | None:
    """Extract Salesforce/CRM case number (6-9 digit number starting with 1)."""
    # Case numbers like 118784423, 116408181
    matches = re.findall(r"\b1[0-9]{8}\b", text)
    # Filter out order IDs (10 digits)
    return matches[0] if matches else None


def extract_order_ids(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\b1[0-9]{9}\b", text)))


def infer_issue_type(subject: str, snippets: list[str]) -> str:
    text = (subject + " " + " ".join(snippets)).lower()
    if "refund" in text:
        return "Refund Issue"
    if "rca" in text:
        return "RCA Required"
    if "damage" in text or "damaged" in text:
        return "Product Damage"
    if "wrong" in text or "mismatch" in text:
        return "Wrong Product"
    if "delay" in text or "not delivered" in text or "pending" in text:
        return "Delivery Delay"
    if "prescription" in text or "power" in text:
        return "Prescription Error"
    if "social media" in text or "instagram" in text:
        return "Social Media Escalation"
    if "partial" in text:
        return "Partial Delivery"
    return "General Escalation"


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
        # Sort emails by date within thread
        thread_emails.sort(key=lambda e: e.get("date", ""))

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
