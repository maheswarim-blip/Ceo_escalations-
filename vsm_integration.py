"""
VSM + CRM Integration - Enriches CEO escalation emails with:
  - Order details from VSM API
  - WhatsApp / CRM comments from CRM API

Usage:
    python3 vsm_integration.py

Output:
    ceo_escalation_emails_enriched.json
"""

import base64
import json
import re
import time
import requests
from pathlib import Path

# ── VSM API config ────────────────────────────────────────────────────────────
VSM_API_BASE = "https://api-gateway.juno.lenskart.com/v2/orders/internal"

VSM_HEADERS = {
    "content-type": "application/json",
    "x-accept-language": "en",
    "x-api-client": "VSM",
    "x-country-code": "IN",
    "X-session-token": "09e5da3d-7334-4d56-bef3-de4e311e9017",
    "X-Auth-Token": "8e8b0816-4c73-4f08-8f7d-022dcd186a91",
    "X-Api-Client": "VSM",
    "Cookie": "__cf_bm=9hlW6eNokAkVmucOM1Dob813pxVwNXBv2E0tipddkZw-1762156379-1.0.1.1-iJ_oZjHeHH0kJTqigdEchpvqHarlNui8wX23h3gHaPoHqqo8M9_asGdCHV8ZdkjgPHoR9.9wCiwfx8zgx.A709HwdUDBhAoSxUrLES6ujXg",
}

# ── CRM API config ─────────────────────────────────────────────────────────────
CRM_API_BASE = "https://crm-jr.scm.lenskart.com/v2"

CRM_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
    "content-type": "application/json",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "x-access-token": "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpYXQiOjE3NzM4MTIyMzksImV4cCI6MTc3MzgzMzgzOSwiZGF0YSI6eyJ1c2VySWQiOjIxOTgyLCJ1c2VybmFtZSI6InBhd2FuLmdvZ2lhQGxlbnNrYXJ0LmluIiwicm9sZSI6MTd9fQ.1CxB4F9eyZMRysCylT-2FKA5tfeeaUwtqxL7d3EA6fI",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_order_ids(text: str) -> list[str]:
    """Extract Lenskart order IDs (10-digit numbers starting with 1) from text."""
    return list(dict.fromkeys(re.findall(r"\b1[0-9]{9}\b", text)))


def extract_phones(text: str) -> list[str]:
    """Extract 10-digit Indian mobile numbers from text."""
    return list(dict.fromkeys(re.findall(r"\b[6-9]\d{9}\b", text)))


# ── Gmail full-body fetcher (OAuth2 mode, optional) ───────────────────────────

def _fetch_gmail_body(email_id: str, creds) -> str:
    """
    Fetch the full plain-text body of a Gmail message.
    Returns empty string on any error.
    """
    if creds is None:
        return ""
    try:
        from googleapiclient.discovery import build as _gbuild
        service = _gbuild("gmail", "v1", credentials=creds, cache_discovery=False)
        raw = service.users().messages().get(
            userId="me", id=email_id, format="full"
        ).execute()
        payload = raw.get("payload", {})
        parts = payload.get("parts") or [payload]
        text_parts = []
        for part in parts:
            if part.get("mimeType", "") in ("text/plain", ""):
                body_data = (part.get("body") or {}).get("data", "")
                if body_data:
                    decoded = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
                    text_parts.append(decoded)
        return "\n".join(text_parts)
    except Exception:
        return ""


def _get_oauth_creds_silent():
    """Return OAuth2 creds if a token file exists, else None (no browser prompt)."""
    token_file = Path(__file__).parent / "gmail_token.json"
    if not token_file.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GRequest
        creds = Credentials.from_authorized_user_file(
            str(token_file), ["https://www.googleapis.com/auth/gmail.readonly"]
        )
        if creds and creds.refresh_token and (not creds.token or creds.expired):
            creds.refresh(GRequest())
        return creds if creds and creds.valid else None
    except Exception:
        return None


def fetch_vsm_orders_by_phone(phone: str) -> list[dict]:
    """Fetch orders for a customer phone number from VSM API."""
    url = f"{VSM_API_BASE}?phone={phone}&limit=5"
    try:
        resp = requests.get(url, headers=VSM_HEADERS, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        results = raw.get("data", raw)
        if isinstance(results, dict):
            results = results.get("result") or results.get("orders") or []
        if not isinstance(results, list):
            return []
        orders = []
        for data in results:
            orders.append({
                "order_id":       data.get("orderId") or data.get("id"),
                "status":         data.get("status") or data.get("orderStatus"),
                "created_at":     data.get("createdAt") or data.get("orderDate"),
                "customer_name":  data.get("customerName") or (data.get("shippingAddress") or {}).get("name"),
                "customer_phone": data.get("customerPhone") or data.get("mobile"),
                "customer_email": data.get("customerEmail") or data.get("email"),
                "store":          data.get("storeName") or data.get("storeCode"),
                "total_amount":   data.get("totalAmount") or data.get("grandTotal"),
                "payment_status": data.get("paymentStatus"),
                "tracking":       data.get("trackingDetails") or data.get("tracking"),
                "items": [
                    {
                        "sku":   item.get("sku") or item.get("productSku"),
                        "name":  item.get("productName") or item.get("name"),
                        "qty":   item.get("qty") or item.get("quantity"),
                        "price": item.get("price") or item.get("rowTotal"),
                    }
                    for item in (data.get("items") or data.get("orderItems") or [])
                ],
            })
        return orders
    except requests.exceptions.HTTPError as e:
        print(f"    [VSM] HTTP {e.response.status_code} for phone {phone}")
        return []
    except Exception as e:
        print(f"    [VSM] Error for phone {phone}: {e}")
        return []


def fetch_vsm_order(order_id: str) -> dict:
    """Fetch order details from VSM API."""
    url = f"{VSM_API_BASE}/{order_id}"
    try:
        resp = requests.get(url, headers=VSM_HEADERS, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        data = raw.get("data", raw).get("result", raw)
        return {
            "order_id":       data.get("orderId") or data.get("id"),
            "status":         data.get("status") or data.get("orderStatus"),
            "created_at":     data.get("createdAt") or data.get("orderDate"),
            "customer_name":  data.get("customerName") or (data.get("shippingAddress") or {}).get("name"),
            "customer_phone": data.get("customerPhone") or data.get("mobile"),
            "customer_email": data.get("customerEmail") or data.get("email"),
            "store":          data.get("storeName") or data.get("storeCode"),
            "total_amount":   data.get("totalAmount") or data.get("grandTotal"),
            "payment_status": data.get("paymentStatus"),
            "tracking":       data.get("trackingDetails") or data.get("tracking"),
            "items": [
                {
                    "sku":   item.get("sku") or item.get("productSku"),
                    "name":  item.get("productName") or item.get("name"),
                    "qty":   item.get("qty") or item.get("quantity"),
                    "price": item.get("price") or item.get("rowTotal"),
                }
                for item in (data.get("items") or data.get("orderItems") or [])
            ],
        }
    except requests.exceptions.HTTPError as e:
        print(f"    [VSM] HTTP {e.response.status_code} for order {order_id}")
        return {"order_id": order_id, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        print(f"    [VSM] Error for order {order_id}: {e}")
        return {"order_id": order_id, "error": str(e)}


def fetch_crm_comments(order_id: str) -> list[dict]:
    """Fetch WhatsApp/CRM comments for an order."""
    url = f"{CRM_API_BASE}/{order_id}/comments"
    try:
        resp = requests.get(url, headers=CRM_HEADERS, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        comments = raw if isinstance(raw, list) else raw.get("data") or raw.get("comments") or []
        return [
            {
                "id":         c.get("id"),
                "type":       c.get("type") or c.get("commentType"),
                "channel":    c.get("channel") or "whatsapp",
                "message":    c.get("message") or c.get("content") or c.get("text"),
                "author":     c.get("author") or c.get("createdBy") or c.get("agentName"),
                "created_at": c.get("createdAt") or c.get("timestamp"),
                "direction":  c.get("direction"),  # inbound / outbound
            }
            for c in comments
        ]
    except requests.exceptions.HTTPError as e:
        print(f"    [CRM] HTTP {e.response.status_code} for order {order_id}")
        return [{"error": f"HTTP {e.response.status_code}"}]
    except Exception as e:
        print(f"    [CRM] Error for order {order_id}: {e}")
        return [{"error": str(e)}]


# ── Incremental enrichment (for pipeline / real-time sync) ───────────────────

def enrich_one_email(
    email: dict,
    vsm_cache: dict,
    crm_cache: dict,
    phone_cache: dict,
    gmail_creds=None,
) -> dict:
    """
    Enrich a single email dict in-place with order_ids, vsm_orders, crm_comments.
    Shared caches are passed in so callers can reuse VSM/CRM results across emails.
    Returns the mutated email dict.
    """
    # Use stored body as part of search text if available
    search_text = (
        f"{email.get('subject', '')} "
        f"{email.get('snippet', '')} "
        f"{email.get('body', '')}"
    )
    order_ids = extract_order_ids(search_text)
    phones    = extract_phones(search_text)

    # Body-based lookup via OAuth (no body stored yet and OAuth available)
    if not order_ids and not phones and gmail_creds and not email.get("body"):
        body = _fetch_gmail_body(email.get("id", ""), gmail_creds)
        if body:
            full_text = search_text + " " + body
            order_ids = extract_order_ids(full_text)
            phones    = extract_phones(full_text)
            if order_ids or phones:
                email["body_preview"] = body[:1500]

    email["order_ids"] = order_ids
    email.setdefault("vsm_orders", [])
    email.setdefault("crm_comments", {})

    if order_ids:
        for oid in order_ids:
            if oid not in vsm_cache:
                vsm_cache[oid] = fetch_vsm_order(oid)
                time.sleep(0.3)
            existing_oids = [str(o.get("order_id")) for o in email["vsm_orders"]]
            if oid not in existing_oids:
                email["vsm_orders"].append(vsm_cache[oid])

            if oid not in crm_cache:
                crm_cache[oid] = fetch_crm_comments(oid)
                time.sleep(0.3)
            email["crm_comments"][oid] = crm_cache[oid]

    elif phones:
        for phone in phones[:2]:
            if phone not in phone_cache:
                phone_cache[phone] = fetch_vsm_orders_by_phone(phone)
                time.sleep(0.3)
            for order in phone_cache[phone]:
                oid = str(order.get("order_id", ""))
                if not oid:
                    continue
                existing_oids = [str(o.get("order_id")) for o in email["vsm_orders"]]
                if oid not in existing_oids:
                    email["vsm_orders"].append(order)
                if oid not in order_ids:
                    order_ids.append(oid)
                if oid not in crm_cache:
                    crm_cache[oid] = fetch_crm_comments(oid)
                    time.sleep(0.3)
                email["crm_comments"][oid] = crm_cache[oid]
        email["order_ids"] = order_ids

    return email


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    emails_file = Path("ceo_escalation_emails.json")
    if not emails_file.exists():
        print("[!] ceo_escalation_emails.json not found.")
        return

    with open(emails_file) as f:
        data = json.load(f)

    emails = data["emails"]
    vsm_cache: dict[str, dict] = {}
    crm_cache: dict[str, list] = {}
    phone_cache: dict[str, list] = {}

    # Silent OAuth creds for full-body fetch (no browser prompt)
    gmail_creds = _get_oauth_creds_silent()
    if gmail_creds:
        print("Gmail OAuth available – will fetch full email body when needed.\n")
    else:
        print("Gmail OAuth not available – using snippet only for order/phone extraction.\n")

    print(f"Processing {len(emails)} emails ...\n")

    for email in emails:
        search_text = f"{email.get('subject', '')} {email.get('snippet', '')}"
        order_ids = extract_order_ids(search_text)
        phones = extract_phones(search_text)

        # If no order IDs or phones in snippet, try fetching full email body
        if not order_ids and not phones and gmail_creds:
            body = _fetch_gmail_body(email.get("id", ""), gmail_creds)
            if body:
                full_text = search_text + " " + body
                order_ids = extract_order_ids(full_text)
                phones = extract_phones(full_text)
                if order_ids or phones:
                    print(f"  [body-fetch] Found in full body — orders: {order_ids} phones: {phones}")
                    # Store the body snippet for RCA context (first 1500 chars)
                    email["body_preview"] = body[:1500]
        email["order_ids"] = order_ids
        email["vsm_orders"] = []
        email["crm_comments"] = {}

        print(f"[EMAIL] {email['subject'][:70]}")

        # ── Order ID based lookup ─────────────────────────────────────────────
        if order_ids:
            print(f"  Order IDs: {order_ids}")
            for oid in order_ids:
                if oid not in vsm_cache:
                    print(f"  → Fetching VSM order {oid}")
                    vsm_cache[oid] = fetch_vsm_order(oid)
                    time.sleep(0.3)
                email["vsm_orders"].append(vsm_cache[oid])

                if oid not in crm_cache:
                    print(f"  → Fetching CRM comments for {oid}")
                    crm_cache[oid] = fetch_crm_comments(oid)
                    time.sleep(0.3)
                email["crm_comments"][oid] = crm_cache[oid]

        # ── Phone-based fallback: try when no order IDs found in this email ──
        elif phones:
            print(f"  No order IDs found. Trying phone lookup: {phones}")
            for phone in phones[:2]:  # limit to first 2 phones
                if phone in phone_cache:
                    phone_orders = phone_cache[phone]
                else:
                    print(f"  → Fetching VSM orders for phone {phone}")
                    phone_orders = fetch_vsm_orders_by_phone(phone)
                    phone_cache[phone] = phone_orders
                    time.sleep(0.3)

                for order in phone_orders:
                    oid = str(order.get("order_id", ""))
                    if not oid:
                        continue
                    if oid not in [str(o.get("order_id")) for o in email["vsm_orders"]]:
                        email["vsm_orders"].append(order)
                        if oid not in order_ids:
                            order_ids.append(oid)
                    if oid not in crm_cache:
                        print(f"  → Fetching CRM comments for {oid}")
                        crm_cache[oid] = fetch_crm_comments(oid)
                        time.sleep(0.3)
                    email["crm_comments"][oid] = crm_cache[oid]

            email["order_ids"] = order_ids  # update with phone-resolved IDs
        else:
            print("  No order IDs or phones found. Skipping.")

        print()

    output = {
        "collected_at":        data["collected_at"],
        "enriched_at":         time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_emails":        len(emails),
        "total_unique_orders": len(vsm_cache),
        "emails":              emails,
    }

    out_file = Path("ceo_escalation_emails_enriched.json")
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nDone! Saved to: {out_file}")
    print(f"  Emails          : {len(emails)}")
    print(f"  Unique orders   : {len(vsm_cache)}")
    print(f"  Orders w/ CRM   : {sum(1 for v in crm_cache.values() if v and 'error' not in v[0])}")


if __name__ == "__main__":
    main()
