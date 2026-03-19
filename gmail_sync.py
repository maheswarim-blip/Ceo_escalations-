"""
gmail_sync.py
=============
Fetches new CEO escalation emails from Gmail via the MCP HTTP endpoint
and updates ceo_escalation_emails.json.

Can be run standalone:
    python gmail_sync.py

Or imported and called from the dashboard:
    from gmail_sync import sync_new_emails
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ── MCP config ─────────────────────────────────────────────────────────────────

MCP_CONFIG_FILE = Path("/tmp/mcp-config-cse_01TfXjzkhihzovci4vgbvWNW.json")
DATA_FILE       = Path(__file__).parent / "ceo_escalation_emails.json"
GMAIL_QUERY     = (
    "to:ceoescalation@lenskart.com OR "
    "cc:ceoescalation@lenskart.com OR "
    "from:ceoescalation@lenskart.com"
)


def _load_mcp_session() -> tuple[str, str, dict]:
    """Return (token, mcp_url, extra_headers) or raise RuntimeError."""
    token_file = os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE", "")
    if not token_file or not os.path.exists(token_file):
        raise RuntimeError(
            "Gmail sync requires CLAUDE_SESSION_INGRESS_TOKEN_FILE env var "
            "(only available inside a Claude Code session)."
        )
    with open(token_file) as f:
        token = f.read().strip()

    if not MCP_CONFIG_FILE.exists():
        raise RuntimeError(f"MCP config not found: {MCP_CONFIG_FILE}")
    with open(MCP_CONFIG_FILE) as f:
        cfg = json.load(f)

    # Pick the first Gmail MCP server
    for server_id, server in cfg["mcpServers"].items():
        tools = [t["name"] for t in server.get("tools", [])]
        if "gmail_search_messages" in tools:
            return token, server["url"], server.get("headers", {})

    raise RuntimeError("No Gmail MCP server found in config.")


def _mcp_call(url: str, headers: dict, method: str, params: dict,
              retries: int = 3) -> dict:
    """Make one MCP JSON-RPC call over HTTP/SSE and return the result."""
    call_id = 1
    payload = {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}

    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=60, stream=True)
            if resp.status_code in (502, 503, 504):
                resp.close()
                time.sleep(2 ** attempt)
                last_err = f"HTTP {resp.status_code}"
                continue
            resp.raise_for_status()
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(2 ** attempt)
            continue

        # Read full SSE stream (may be large)
        buf = b""
        for chunk in resp.iter_content(chunk_size=65536):
            buf += chunk
        resp.close()

        raw_text = buf.decode("utf-8", errors="replace")

        # Each SSE message starts with "data: " — collect full data payload
        data_chunks = []
        in_data = False
        for line in raw_text.splitlines():
            if line.startswith("data:"):
                payload_piece = line[5:]
                if payload_piece.strip():
                    data_chunks.append(payload_piece)
                    in_data = True
            elif line == "" and in_data:
                # End of SSE event — parse accumulated data
                full_data = "".join(data_chunks).strip()
                try:
                    msg = json.loads(full_data)
                    if "error" in msg:
                        raise RuntimeError(f"MCP error: {msg['error']}")
                    result = msg.get("result")
                    if result is not None:
                        return result
                except json.JSONDecodeError:
                    pass  # partial chunk, keep going
                data_chunks = []
                in_data = False

        # Try remaining buffer if no blank line at end
        if data_chunks:
            full_data = "".join(data_chunks).strip()
            try:
                msg = json.loads(full_data)
                if "error" in msg:
                    raise RuntimeError(f"MCP error: {msg['error']}")
                result = msg.get("result")
                if result is not None:
                    return result
            except json.JSONDecodeError:
                last_err = "JSON parse error in SSE response"
                continue

        last_err = "No result found in MCP response"

    raise RuntimeError(f"MCP call failed after {retries} retries: {last_err}")


def _search_gmail(url: str, headers: dict, max_results: int = 500,
                  page_token: str = None) -> dict:
    args = {"q": GMAIL_QUERY, "maxResults": max_results}
    if page_token:
        args["pageToken"] = page_token
    result = _mcp_call(url, headers, "tools/call", {
        "name": "gmail_search_messages",
        "arguments": args,
    })
    content = result.get("content", [{}])
    return json.loads(content[0].get("text", "{}"))


def _msg_to_email(msg: dict) -> dict:
    h = msg.get("headers", {})
    return {
        "id":       msg["messageId"],
        "threadId": msg["threadId"],
        "date":     h.get("Date", ""),
        "from":     h.get("From", ""),
        "to":       h.get("To", ""),
        "cc":       h.get("Cc", ""),
        "subject":  h.get("Subject", ""),
        "snippet":  msg.get("snippet", ""),
        "labels":   msg.get("labelIds", []),
    }


def sync_new_emails(verbose: bool = False) -> dict:
    """
    Fetch all Gmail messages and merge new ones into the local JSON file.

    Returns a summary dict:
        {
            "new_count": int,
            "total": int,
            "latest_date": str,
            "synced_at": str,
        }
    """
    token, url, extra_headers = _load_mcp_session()
    http_headers = {
        **extra_headers,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "anthropic-version": "2023-06-01",
    }

    # Load existing data
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            data = json.load(f)
    else:
        data = {"collected_at": "", "query": GMAIL_QUERY,
                "total_estimated": 0, "total_collected": 0, "emails": []}

    existing_ids = {e["id"] for e in data.get("emails", [])}

    # Fetch from Gmail (paginate)
    all_messages = []
    page_token = None
    while True:
        page = _search_gmail(url, http_headers, max_results=500, page_token=page_token)
        msgs = page.get("messages", [])
        all_messages.extend(msgs)
        if verbose:
            print(f"  Fetched {len(all_messages)} messages so far…", flush=True)
        page_token = page.get("nextPageToken")
        if not page_token:
            break

    # Find truly new messages
    new_emails = [
        _msg_to_email(m)
        for m in all_messages
        if m["messageId"] not in existing_ids
    ]

    if new_emails:
        # Prepend newest first
        data["emails"] = new_emails + data["emails"]
        data["total_collected"] = len(data["emails"])
        data["query"] = GMAIL_QUERY

    now = datetime.now().isoformat(timespec="seconds")
    data["synced_at"] = now
    data["total_estimated"] = page.get("resultSizeEstimate", len(all_messages))

    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    latest = data["emails"][0]["date"] if data["emails"] else ""
    summary = {
        "new_count":   len(new_emails),
        "total":       data["total_collected"],
        "latest_date": latest,
        "synced_at":   now,
    }
    if verbose:
        print(f"Sync complete: {len(new_emails)} new emails → {data['total_collected']} total")
    return summary


if __name__ == "__main__":
    try:
        result = sync_new_emails(verbose=True)
        print(json.dumps(result, indent=2))
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
