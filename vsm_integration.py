"""
VSM + CRM Integration - Enriches CEO escalation emails with:
  - Order details from VSM API
  - WhatsApp / CRM comments from CRM API

Usage:
    python3 vsm_integration.py

Output:
    ceo_escalation_emails_enriched.json
"""

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


def fetch_vsm_order(order_id: str) -> dict:
    """Fetch order details from VSM API."""
    url = f"{VSM_API_BASE}/{order_id}"
    try:
        resp = requests.get(url, headers=VSM_HEADERS, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        data = raw.get("data", raw)
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

    print(f"Processing {len(emails)} emails ...\n")

    for email in emails:
        search_text = f"{email.get('subject', '')} {email.get('snippet', '')}"
        order_ids = extract_order_ids(search_text)
        email["order_ids"] = order_ids
        email["vsm_orders"] = []
        email["crm_comments"] = {}

        if not order_ids:
            continue

        print(f"[EMAIL] {email['subject'][:70]}")
        print(f"  Order IDs: {order_ids}")

        for oid in order_ids:
            # VSM order details
            if oid not in vsm_cache:
                print(f"  → Fetching VSM order {oid}")
                vsm_cache[oid] = fetch_vsm_order(oid)
                time.sleep(0.3)
            email["vsm_orders"].append(vsm_cache[oid])

            # CRM comments (WhatsApp)
            if oid not in crm_cache:
                print(f"  → Fetching CRM comments for {oid}")
                crm_cache[oid] = fetch_crm_comments(oid)
                time.sleep(0.3)
            email["crm_comments"][oid] = crm_cache[oid]

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
