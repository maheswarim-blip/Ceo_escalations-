"""
gmail_sync.py
=============
Fetches new CEO escalation emails from Gmail and updates
ceo_escalation_emails.json.

Two modes (auto-detected):
  1. Claude Code session (server) – uses the built-in MCP bridge, no setup needed.
  2. Local / Mac – uses Gmail API with OAuth2. Requires one-time setup:
       pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
       python gmail_sync.py --setup      # opens browser for Google sign-in

Run:
    python gmail_sync.py            # sync emails
    python gmail_sync.py --setup    # first-time OAuth2 setup (local mode only)
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

DATA_FILE   = Path(__file__).parent / "ceo_escalation_emails.json"
TOKEN_FILE  = Path(__file__).parent / "gmail_token.json"
GMAIL_QUERY = (
    "to:ceoescalation@lenskart.com OR "
    "cc:ceoescalation@lenskart.com OR "
    "from:ceoescalation@lenskart.com"
)

# Gmail OAuth2 scopes needed (read-only is enough)
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


# ── Mode detection ──────────────────────────────────────────────────────────────

def _in_claude_session() -> bool:
    token_file = os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE", "")
    return bool(token_file and Path(token_file).exists())


# ══════════════════════════════════════════════════════════════════════════════
# MODE 1: Claude Code MCP bridge (server environment)
# ══════════════════════════════════════════════════════════════════════════════

def _load_mcp_session():
    token_file = os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE", "")
    with open(token_file) as f:
        token = f.read().strip()

    # Find MCP config (filename contains session ID)
    configs = list(Path("/tmp").glob("mcp-config-*.json"))
    if not configs:
        raise RuntimeError("MCP config file not found in /tmp.")
    with open(configs[0]) as f:
        cfg = json.load(f)

    for server in cfg["mcpServers"].values():
        tools = [t["name"] for t in server.get("tools", [])]
        if "gmail_search_messages" in tools:
            return token, server["url"], server.get("headers", {})

    raise RuntimeError("No Gmail MCP server found in config.")


def _mcp_call(url, headers, method, params, retries=3):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
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


def _search_via_mcp(url, headers, max_results=500, page_token=None):
    args = {"q": GMAIL_QUERY, "maxResults": max_results}
    if page_token:
        args["pageToken"] = page_token
    result = _mcp_call(url, headers, "tools/call",
                       {"name": "gmail_search_messages", "arguments": args})
    content = result.get("content", [{}])
    return json.loads(content[0].get("text", "{}"))


def _sync_via_mcp(verbose=False):
    token, url, extra_headers = _load_mcp_session()
    http_headers = {
        **extra_headers,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "anthropic-version": "2023-06-01",
    }
    all_messages, page_token = [], None
    while True:
        page = _search_via_mcp(url, http_headers, page_token=page_token)
        all_messages.extend(page.get("messages", []))
        if verbose:
            print(f"  Fetched {len(all_messages)} messages…", flush=True)
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    return all_messages, page.get("resultSizeEstimate", len(all_messages))


# ══════════════════════════════════════════════════════════════════════════════
# MODE 2: Local OAuth2 via Gmail REST API
# ══════════════════════════════════════════════════════════════════════════════

def _get_oauth_creds():
    """Load or refresh OAuth2 credentials from gmail_token.json."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GRequest
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise RuntimeError(
            "Missing Google libraries. Install them:\n"
            "  pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client"
        )

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GRequest())
        else:
            creds_file = Path(__file__).parent / "credentials.json"
            if not creds_file.exists():
                raise RuntimeError(
                    "credentials.json not found.\n\n"
                    "To set up Gmail access on your Mac:\n"
                    "  1. Go to https://console.cloud.google.com/\n"
                    "  2. Create a project → Enable Gmail API\n"
                    "  3. Create OAuth2 credentials (Desktop app type)\n"
                    "  4. Download as credentials.json into this folder\n"
                    "  5. Run: python gmail_sync.py --setup"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


def _search_via_oauth(creds, page_token=None, max_results=500):
    """Call Gmail REST API with OAuth2 token."""
    params = {
        "q": GMAIL_QUERY,
        "maxResults": min(max_results, 500),
        "fields": "messages(id,threadId),nextPageToken,resultSizeEstimate",
    }
    if page_token:
        params["pageToken"] = page_token

    headers = {"Authorization": f"Bearer {creds.token}"}
    resp = requests.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        headers=headers, params=params, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def _get_message_meta(creds, msg_id):
    """Fetch message metadata (headers + snippet) via REST API."""
    headers = {"Authorization": f"Bearer {creds.token}"}
    resp = requests.get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
        headers=headers,
        params={"format": "metadata",
                "metadataHeaders": "From,To,Cc,Subject,Date",
                "fields": "id,threadId,labelIds,snippet,payload/headers"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_oauth_message(raw: dict) -> dict:
    headers = {h["name"]: h["value"]
               for h in raw.get("payload", {}).get("headers", [])}
    return {
        "id":       raw["id"],
        "threadId": raw["threadId"],
        "date":     headers.get("Date", ""),
        "from":     headers.get("From", ""),
        "to":       headers.get("To", ""),
        "cc":       headers.get("Cc", ""),
        "subject":  headers.get("Subject", ""),
        "snippet":  raw.get("snippet", ""),
        "labels":   raw.get("labelIds", []),
    }


def _sync_via_oauth(verbose=False):
    creds = _get_oauth_creds()
    # Refresh token if close to expiry
    from google.auth.transport.requests import Request as GRequest
    if creds.expired:
        creds.refresh(GRequest())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    # Get list of message IDs
    all_ids, page_token, result_size = [], None, 0
    while True:
        page = _search_via_oauth(creds, page_token=page_token)
        all_ids.extend(page.get("messages", []))
        result_size = page.get("resultSizeEstimate", len(all_ids))
        if verbose:
            print(f"  Found {len(all_ids)} message IDs so far…", flush=True)
        page_token = page.get("nextPageToken")
        if not page_token:
            break

    # Load existing IDs to avoid re-fetching metadata
    existing_ids: set = set()
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            existing_ids = {e["id"] for e in json.load(f).get("emails", [])}

    # Fetch metadata only for new messages
    new_raw = []
    for i, m in enumerate(all_ids):
        if m["id"] in existing_ids:
            continue
        meta = _get_message_meta(creds, m["id"])
        new_raw.append(meta)
        if verbose and (i + 1) % 20 == 0:
            print(f"  Fetched metadata for {i+1}/{len(all_ids)}…", flush=True)

    # Build fake "messages" list in MCP format for reuse of _msg_to_email
    all_messages_mcp = []
    for raw in new_raw:
        h = {hh["name"]: hh["value"]
             for hh in raw.get("payload", {}).get("headers", [])}
        all_messages_mcp.append({
            "messageId": raw["id"],
            "threadId":  raw["threadId"],
            "labelIds":  raw.get("labelIds", []),
            "snippet":   raw.get("snippet", ""),
            "headers":   h,
        })

    return all_messages_mcp, result_size


# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def sync_new_emails(verbose: bool = False) -> dict:
    """
    Fetch new CEO escalation emails and merge into ceo_escalation_emails.json.
    Auto-detects Claude Code session vs local OAuth2 mode.
    """
    # Load existing data
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            data = json.load(f)
    else:
        data = {"collected_at": "", "query": GMAIL_QUERY,
                "total_estimated": 0, "total_collected": 0, "emails": []}

    existing_ids = {e["id"] for e in data.get("emails", [])}

    # Pick sync method
    if _in_claude_session():
        if verbose:
            print("Mode: Claude Code MCP session")
        all_messages, result_size = _sync_via_mcp(verbose=verbose)
    else:
        if verbose:
            print("Mode: Local Gmail OAuth2")
        all_messages, result_size = _sync_via_oauth(verbose=verbose)

    # Merge new emails
    new_emails = [
        _msg_to_email(m)
        for m in all_messages
        if m["messageId"] not in existing_ids
    ]

    if new_emails:
        data["emails"] = new_emails + data["emails"]
        data["total_collected"] = len(data["emails"])
        data["query"] = GMAIL_QUERY

    now = datetime.now().isoformat(timespec="seconds")
    data["synced_at"] = now
    data["total_estimated"] = result_size

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
        print(f"Sync complete: {len(new_emails)} new → {data['total_collected']} total")
    return summary


def setup_oauth():
    """Run the one-time OAuth2 browser flow to generate gmail_token.json."""
    print("Opening browser for Google sign-in…")
    _get_oauth_creds()
    print(f"Done! Token saved to {TOKEN_FILE}")
    print("You can now run: python gmail_sync.py")


if __name__ == "__main__":
    if "--setup" in sys.argv:
        try:
            setup_oauth()
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            result = sync_new_emails(verbose=True)
            print(json.dumps(result, indent=2))
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
