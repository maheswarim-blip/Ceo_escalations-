"""
backfill_bodies.py
==================
Fetches full email bodies for all emails in the JSON files that don't have one yet.
Uses the Gmail MCP bridge (Claude Code session required).

Usage:
    python3 backfill_bodies.py
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

DATA_FILE     = Path("ceo_escalation_emails.json")
ENRICHED_FILE = Path("ceo_escalation_emails_enriched.json")


def _load_mcp_session():
    token_file = os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE", "")
    with open(token_file) as f:
        token = f.read().strip()
    configs = list(Path("/tmp").glob("mcp-config-*.json"))
    if not configs:
        raise RuntimeError("MCP config file not found in /tmp.")
    with open(configs[0]) as f:
        cfg = json.load(f)
    for server in cfg["mcpServers"].values():
        tools = [t["name"] for t in server.get("tools", [])]
        if "gmail_read_message" in tools:
            return token, server["url"], server.get("headers", {})
    raise RuntimeError("No Gmail MCP server found.")


def _mcp_call(url, headers, method, params, retries=4):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=60, stream=True)
            if resp.status_code in (502, 503, 504):
                resp.close()
                wait = 2 ** attempt
                print(f"    HTTP {resp.status_code}, retrying in {wait}s…")
                time.sleep(wait)
                last_err = f"HTTP {resp.status_code}"
                continue
            resp.raise_for_status()
        except requests.RequestException as e:
            last_err = str(e)
            wait = 2 ** attempt
            time.sleep(wait)
            continue

        buf = b""
        for chunk in resp.iter_content(chunk_size=65536):
            buf += chunk
        resp.close()
        raw_text = buf.decode("utf-8", errors="replace")

        data_chunks, in_data = [], False
        for line in raw_text.splitlines():
            if line.startswith("data:"):
                piece = line[5:]
                if piece.strip():
                    data_chunks.append(piece)
                    in_data = True
            elif line == "" and in_data:
                try:
                    msg = json.loads("".join(data_chunks).strip())
                    if "error" in msg:
                        raise RuntimeError(f"MCP error: {msg['error']}")
                    if msg.get("result") is not None:
                        return msg["result"]
                except json.JSONDecodeError:
                    pass
                data_chunks, in_data = [], False

        if data_chunks:
            try:
                msg = json.loads("".join(data_chunks).strip())
                if "error" in msg:
                    raise RuntimeError(f"MCP error: {msg['error']}")
                if msg.get("result") is not None:
                    return msg["result"]
            except json.JSONDecodeError:
                last_err = "JSON parse error"
                continue

        last_err = "No result in MCP response"

    raise RuntimeError(f"MCP call failed after {retries} retries: {last_err}")


def fetch_body(url, http_headers, msg_id: str) -> str:
    """Return the plain-text body for a Gmail message ID. Empty string on error."""
    try:
        result = _mcp_call(url, http_headers, "tools/call",
                           {"name": "gmail_read_message",
                            "arguments": {"messageId": msg_id}})
        content = result.get("content", [{}])
        msg = json.loads(content[0].get("text", "{}"))
        return msg.get("body", "")
    except Exception as e:
        print(f"    [WARN] Failed to fetch {msg_id}: {e}")
        return ""


def main():
    if not os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE"):
        print("ERROR: Not in a Claude Code session. Run from within Claude Code.")
        sys.exit(1)

    token, url, extra_headers = _load_mcp_session()
    http_headers = {
        **extra_headers,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "anthropic-version": "2023-06-01",
    }

    # Load both JSON files
    if not DATA_FILE.exists():
        print("ERROR: ceo_escalation_emails.json not found.")
        sys.exit(1)
    with open(DATA_FILE) as f:
        raw_data = json.load(f)

    enriched_data = None
    if ENRICHED_FILE.exists():
        with open(ENRICHED_FILE) as f:
            enriched_data = json.load(f)

    raw_emails    = raw_data.get("emails", [])
    enriched_emails = enriched_data.get("emails", []) if enriched_data else []

    # Build index for enriched emails
    enriched_by_id = {e["id"]: e for e in enriched_emails}

    # Find emails missing body
    missing = [e for e in raw_emails if not e.get("body")]
    total   = len(raw_emails)
    print(f"Emails: {total} total, {len(missing)} missing body, {total - len(missing)} already have body")
    print(f"Fetching bodies via Gmail MCP...\n")

    filled = 0
    failed = 0
    for i, email in enumerate(missing):
        mid = email["id"]
        body = fetch_body(url, http_headers, mid)
        if body:
            email["body"] = body
            # Mirror to enriched JSON
            if mid in enriched_by_id:
                enriched_by_id[mid]["body"] = body
            filled += 1
        else:
            failed += 1

        # Progress
        done = i + 1
        pct  = done / len(missing) * 100
        print(f"  [{done:3d}/{len(missing)}] {pct:5.1f}%  {mid}  {'OK' if body else 'FAILED'}")

        # Save checkpoint every 50 emails
        if done % 50 == 0:
            with open(DATA_FILE, "w") as f:
                json.dump(raw_data, f, indent=2, ensure_ascii=False)
            if enriched_data:
                with open(ENRICHED_FILE, "w") as f:
                    json.dump(enriched_data, f, indent=2, ensure_ascii=False)
            print(f"  [checkpoint saved at {done}]\n")

        time.sleep(0.1)   # gentle pacing

    # Final save
    with open(DATA_FILE, "w") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False)
    if enriched_data:
        with open(ENRICHED_FILE, "w") as f:
            json.dump(enriched_data, f, indent=2, ensure_ascii=False)

    print(f"\nDone! Filled: {filled}, Failed: {failed}")


if __name__ == "__main__":
    main()
