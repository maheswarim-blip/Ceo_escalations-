"""
pipeline.py
===========
Full real-time sync pipeline for the CEO Escalation dashboard.

Chains three steps in one call:
  1. Gmail sync       — pull new emails from ceoescalation@lenskart.com
  2. Body fetch       — store full email body for every new message (MCP mode)
  3. VSM enrichment   — fetch VSM order details + CRM comments for new emails only

Only new emails (not already in enriched JSON) are processed in steps 2 & 3,
so the pipeline is safe to call repeatedly without re-fetching known data.

Usage (from dashboard or CLI):
    from pipeline import run_full_sync
    result = run_full_sync(verbose=True)

CLI:
    python3 pipeline.py
"""

import json
import time
from datetime import datetime
from pathlib import Path

ENRICHED_FILE = Path("ceo_escalation_emails_enriched.json")
RAW_FILE      = Path("ceo_escalation_emails.json")


def run_full_sync(verbose: bool = False) -> dict:
    """
    Run the complete sync pipeline:
      Gmail sync → body fetch → incremental VSM enrichment

    Returns a summary dict:
      {
        new_gmail:   int,   # new emails fetched from Gmail
        new_enriched: int,  # new emails enriched with VSM/CRM
        total:       int,   # total emails in enriched JSON
        synced_at:   str,
        errors:      list,
      }
    """
    errors = []
    new_gmail   = 0
    new_enriched = 0

    # ── Step 1: Gmail sync ────────────────────────────────────────────────────
    if verbose:
        print("[1/3] Syncing Gmail…")
    try:
        from gmail_sync import sync_new_emails
        sync_result = sync_new_emails(verbose=False)
        new_gmail   = sync_result.get("new_count", 0)
        if verbose:
            print(f"      +{new_gmail} new emails (total {sync_result.get('total', '?')})")
    except Exception as e:
        errors.append(f"Gmail sync failed: {e}")
        if verbose:
            print(f"      ERROR: {e}")

    # ── Step 2: Body fetch for new emails (MCP mode) ──────────────────────────
    if verbose:
        print("[2/3] Fetching email bodies for new messages…")
    try:
        _backfill_new_bodies(verbose=verbose)
    except Exception as e:
        errors.append(f"Body fetch failed: {e}")
        if verbose:
            print(f"      WARN: {e}")

    # ── Step 3: Incremental VSM enrichment ────────────────────────────────────
    if verbose:
        print("[3/3] Running incremental VSM enrichment…")
    try:
        new_enriched = _enrich_new_emails(verbose=verbose)
        if verbose:
            print(f"      Enriched {new_enriched} new email(s)")
    except Exception as e:
        errors.append(f"VSM enrichment failed: {e}")
        if verbose:
            print(f"      ERROR: {e}")

    # Count total in enriched file
    total = 0
    if ENRICHED_FILE.exists():
        with open(ENRICHED_FILE) as f:
            total = len(json.load(f).get("emails", []))

    synced_at = datetime.now().isoformat(timespec="seconds")
    if verbose:
        print(f"\nSync complete at {synced_at}")
        print(f"  New from Gmail : {new_gmail}")
        print(f"  Newly enriched : {new_enriched}")
        print(f"  Total in dashboard: {total}")
        if errors:
            print(f"  Errors: {errors}")

    return {
        "new_gmail":    new_gmail,
        "new_enriched": new_enriched,
        "total":        total,
        "synced_at":    synced_at,
        "errors":       errors,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _backfill_new_bodies(verbose: bool = False):
    """
    Fetch full body for emails that are in ceo_escalation_emails.json
    but don't have a body yet. Works in Claude Code MCP session only.
    """
    import os
    if not os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE"):
        return  # not in MCP session — skip silently

    if not RAW_FILE.exists():
        return

    with open(RAW_FILE) as f:
        raw_data = json.load(f)

    missing = [e for e in raw_data.get("emails", []) if not e.get("body")]
    if not missing:
        return

    if verbose:
        print(f"      Fetching bodies for {len(missing)} email(s)…")

    # Re-use backfill_bodies logic from backfill_bodies.py
    from backfill_bodies import fetch_body, _load_mcp_session
    import requests as _req

    token, url, extra_headers = _load_mcp_session()
    http_headers = {
        **extra_headers,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "anthropic-version": "2023-06-01",
    }

    # Load enriched to mirror bodies there too
    enriched_by_id = {}
    if ENRICHED_FILE.exists():
        with open(ENRICHED_FILE) as f:
            enriched_data = json.load(f)
        enriched_by_id = {e["id"]: e for e in enriched_data.get("emails", [])}

    filled = 0
    for email in missing:
        body = fetch_body(url, http_headers, email["id"])
        if body:
            email["body"] = body
            if email["id"] in enriched_by_id:
                enriched_by_id[email["id"]]["body"] = body
            filled += 1
        time.sleep(0.1)

    # Save
    with open(RAW_FILE, "w") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False)

    if ENRICHED_FILE.exists() and filled > 0:
        with open(ENRICHED_FILE) as f:
            enriched_data = json.load(f)
        for e in enriched_data.get("emails", []):
            if e["id"] in enriched_by_id and enriched_by_id[e["id"]].get("body"):
                e["body"] = enriched_by_id[e["id"]]["body"]
        with open(ENRICHED_FILE, "w") as f:
            json.dump(enriched_data, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"      Fetched bodies for {filled}/{len(missing)} email(s)")


def _enrich_new_emails(verbose: bool = False) -> int:
    """
    Find emails in ceo_escalation_emails.json that are NOT yet in
    ceo_escalation_emails_enriched.json and run VSM/CRM enrichment on them.

    Prepends new enriched emails to the enriched JSON so they appear first.
    Returns count of newly enriched emails.
    """
    if not RAW_FILE.exists():
        return 0

    with open(RAW_FILE) as f:
        raw_data = json.load(f)

    # Load or initialise enriched file
    if ENRICHED_FILE.exists():
        with open(ENRICHED_FILE) as f:
            enriched_data = json.load(f)
    else:
        enriched_data = {
            "collected_at":        raw_data.get("collected_at", ""),
            "enriched_at":         "",
            "total_emails":        0,
            "total_unique_orders": 0,
            "emails":              [],
        }

    enriched_ids = {e["id"] for e in enriched_data.get("emails", [])}
    new_emails   = [e for e in raw_data.get("emails", []) if e["id"] not in enriched_ids]

    if not new_emails:
        return 0

    if verbose:
        print(f"      {len(new_emails)} new email(s) to enrich")

    from vsm_integration import enrich_one_email, _get_oauth_creds_silent

    # Seed caches from existing enriched data to avoid re-fetching known orders
    vsm_cache:   dict = {}
    crm_cache:   dict = {}
    phone_cache: dict = {}
    for e in enriched_data.get("emails", []):
        for order in e.get("vsm_orders", []):
            oid = str(order.get("order_id", ""))
            if oid:
                vsm_cache[oid] = order
        for oid, comments in (e.get("crm_comments") or {}).items():
            crm_cache[str(oid)] = comments

    gmail_creds = _get_oauth_creds_silent()

    newly_enriched = []
    for i, email in enumerate(new_emails):
        if verbose:
            print(f"      [{i+1}/{len(new_emails)}] {email.get('subject','')[:60]}")
        try:
            enrich_one_email(email, vsm_cache, crm_cache, phone_cache, gmail_creds)
        except Exception as ex:
            if verbose:
                print(f"        WARN: {ex}")
        newly_enriched.append(email)

    # Prepend new emails (newest first in enriched file)
    enriched_data["emails"] = newly_enriched + enriched_data["emails"]
    enriched_data["total_emails"]        = len(enriched_data["emails"])
    enriched_data["total_unique_orders"] = len(vsm_cache)
    enriched_data["enriched_at"]         = datetime.now().isoformat(timespec="seconds")

    with open(ENRICHED_FILE, "w") as f:
        json.dump(enriched_data, f, indent=2, ensure_ascii=False)

    return len(newly_enriched)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = run_full_sync(verbose=True)
    import json as _j
    print(_j.dumps(result, indent=2))
