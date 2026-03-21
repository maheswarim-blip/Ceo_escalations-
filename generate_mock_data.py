"""
Generates mock enriched data (VSM + CRM) from ceo_escalation_emails.json.
Run this if you can't reach the live VSM/CRM APIs (sandbox / offline).

Output: ceo_escalation_emails_enriched.json
"""

import json, re, random, time
from pathlib import Path
from datetime import datetime, timedelta

random.seed(42)

# ── helpers ────────────────────────────────────────────────────────────────────

NAMES = ["Rajesh Kumar","Priya Singh","Amit Sharma","Sunita Patel",
         "Vikram Nair","Ananya Rao","Deepak Verma","Meera Joshi",
         "Sanjay Gupta","Pooja Mehta","Arjun Reddy","Kavita Das"]

# Cities mapped to zones so generated data populates zone charts correctly
CITY_ZONES = {
    # North
    "Delhi": "North", "Lucknow": "North", "Jaipur": "North",
    "Chandigarh": "North", "Agra": "North",
    # South
    "Bengaluru": "South", "Hyderabad": "South", "Chennai": "South",
    "Kochi": "South", "Coimbatore": "South",
    # West
    "Mumbai": "West", "Pune": "West", "Ahmedabad": "West",
    "Surat": "West", "Nagpur": "West",
    # East
    "Kolkata": "East", "Bhubaneswar": "East", "Patna": "East",
    "Guwahati": "East", "Ranchi": "East",
}
CITIES = list(CITY_ZONES.keys())

FRAME_BRANDS = ["Ray-Ban","Oakley","Vogue","Lenskart Own","Fastrack",
                "Titan","Fossil","Tommy Hilfiger","Calvin Klein","Guess"]

LENS_TYPES = ["Single Vision","Bifocal","Progressive","Blue Cut",
              "Photochromic","Toric","Anti-glare Premium"]

STATUSES = ["DELIVERED","DISPATCHED","PROCESSING","RETURNED",
            "CANCELLED","PARTIALLY_DELIVERED","ON_HOLD"]

PAYMENT_STATUSES = ["PAID","PARTIALLY_PAID","REFUND_INITIATED","REFUNDED","PENDING"]

ISSUE_TYPES = ["Wrong product delivered","Lens mismatch","Frame damage in transit",
               "Prescription error","Missing items","Delayed delivery",
               "Refund not processed","Duplicate order","Power mismatch",
               "Customer dissatisfied with fit"]

RESOLUTIONS = ["Refund initiated","Replacement dispatched","Visit scheduled",
               "Escalated to store manager","Awaiting customer response",
               "Resolved - customer satisfied","Exchange processed"]

AGENTS = ["Rahul Sharma","Priya Verma","Deepak Nair","Anita Patel",
          "Saurabh Gupta","Neha Singh","Vivek Rao"]

def rand_date(days_back=30):
    d = datetime.now() - timedelta(days=random.randint(1, days_back),
                                   hours=random.randint(0,23),
                                   minutes=random.randint(0,59))
    return d.strftime("%Y-%m-%dT%H:%M:%S")

def mock_vsm_order(order_id: str) -> dict:
    status = random.choice(STATUSES)
    customer = random.choice(NAMES)
    city = random.choice(CITIES)
    frame = random.choice(FRAME_BRANDS)
    lens = random.choice(LENS_TYPES)
    frame_price = random.choice([1299,1499,1999,2499,2999,3499,4999,5999,7999])
    lens_price  = random.choice([499, 699, 899, 1199, 1499, 1999])
    total = frame_price + lens_price

    return {
        "order_id":       order_id,
        "status":         status,
        "created_at":     rand_date(60),
        "customer_name":  customer,
        "customer_phone": f"9{random.randint(100000000,999999999)}",
        "customer_email": f"{customer.lower().replace(' ','.')}@gmail.com",
        "store":          f"LKST{random.randint(100,999)} {city}",
        "total_amount":   total,
        "payment_status": random.choice(PAYMENT_STATUSES),
        "tracking": {
            "courier": random.choice(["Delhivery","BlueDart","DTDC","Ekart","Xpressbees"]),
            "awb": f"AWB{random.randint(100000000,999999999)}",
            "status": status,
            "updated_at": rand_date(10),
        },
        "items": [
            {
                "sku":   f"FR-{frame[:3].upper()}-{random.randint(1000,9999)}",
                "name":  f"{frame} {random.choice(['Full Rim','Half Rim','Rimless'])} Frame",
                "qty":   1,
                "price": frame_price,
            },
            {
                "sku":   f"LN-{lens[:3].upper()}-{random.randint(1000,9999)}",
                "name":  f"{lens} Lens",
                "qty":   1,
                "price": lens_price,
            },
        ],
        "issue_type": random.choice(ISSUE_TYPES),
        "whatsapp_thread": f"WA{random.randint(100000,999999)}",
    }


def mock_crm_comments(order_id: str) -> list:
    n = random.randint(3, 8)
    comments = []
    ts = datetime.now() - timedelta(days=random.randint(5,20))
    directions = ["inbound","outbound"]

    for i in range(n):
        ts += timedelta(hours=random.randint(1,12), minutes=random.randint(0,59))
        direction = directions[i % 2]
        if direction == "inbound":
            msg_options = [
                "My order hasn't arrived yet, it's been 15 days!",
                "I received wrong product, this is not what I ordered.",
                "The frame is damaged. I am very unhappy with this.",
                "Please refund my money. This is unacceptable.",
                "I want to escalate this to the CEO team.",
                "Still waiting for resolution. No one is responding.",
            ]
            author = "Customer"
        else:
            msg_options = [
                f"Dear customer, we apologize for the inconvenience. Our team is looking into order {order_id}.",
                "We have escalated your issue to the concerned team. You will hear from us in 24 hours.",
                "As per our records, the order was dispatched. We are checking with the courier.",
                "We are arranging a replacement for you. Please allow 2-3 business days.",
                f"A refund of ₹{random.randint(500,5000)} has been initiated. It will reflect in 5-7 days.",
                "Our store team will contact you shortly to resolve this.",
            ]
            author = random.choice(AGENTS)

        comments.append({
            "id": f"CMT-{order_id}-{i+1:03d}",
            "type": "whatsapp" if i < n//2 else "email",
            "channel": "whatsapp",
            "message": random.choice(msg_options),
            "author": author,
            "created_at": ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "direction": direction,
        })

    return comments


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    src = Path("ceo_escalation_emails.json")
    if not src.exists():
        print("[!] ceo_escalation_emails.json not found")
        return

    with open(src) as f:
        data = json.load(f)

    emails = data["emails"]
    vsm_cache: dict = {}
    crm_cache: dict = {}

    for email in emails:
        text = f"{email.get('subject','')} {email.get('snippet','')}"
        order_ids = list(dict.fromkeys(re.findall(r"\b1[0-9]{9}\b", text)))
        email["order_ids"] = order_ids
        email["vsm_orders"] = []
        email["crm_comments"] = {}

        for oid in order_ids:
            if oid not in vsm_cache:
                vsm_cache[oid] = mock_vsm_order(oid)
            if oid not in crm_cache:
                crm_cache[oid] = mock_crm_comments(oid)
            email["vsm_orders"].append(vsm_cache[oid])
            email["crm_comments"][oid] = crm_cache[oid]

    output = {
        "collected_at":        data["collected_at"],
        "enriched_at":         time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_emails":        len(emails),
        "total_unique_orders": len(vsm_cache),
        "emails":              emails,
    }

    out = Path("ceo_escalation_emails_enriched.json")
    with open(out, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"✓ Generated mock enriched data → {out}")
    print(f"  Emails: {len(emails)}  |  Orders: {len(vsm_cache)}")


if __name__ == "__main__":
    main()
