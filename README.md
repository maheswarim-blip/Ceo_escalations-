# CEO Escalation Dashboard

A Streamlit dashboard for monitoring and analysing CEO escalation emails at Lenskart. It pulls emails from Gmail, enriches them with VSM order data and CRM comments, groups them into cases, and uses Claude to generate Root Cause Analysis (RCA).

---

## How it works

```
Gmail (ceoescalation@lenskart.com)
        │
        ▼
  gmail_sync.py        ← pulls new emails, saves to ceo_escalation_emails.json
        │
        ▼
  vsm_integration.py   ← fetches VSM order + CRM data for each email
        │
        ▼
  ceo_escalation_emails_enriched.json   ← the main data file
        │
        ▼
  case_builder.py      ← groups emails into cases by thread / subject
        │
        ▼
  dashboard.py         ← Streamlit UI + zone charts + Claude RCA
```

The pipeline runs incrementally — only new emails are enriched on each run, so it is safe to trigger repeatedly.

---

## Quick start

### 1. Clone and install

```bash
git clone <repo-url>
cd Ceo_escalations-
pip install -r requirements.txt
```

### 2. Set environment variables

Create a `.env` file (or export to your shell):

```env
# Required for AI-powered RCA
ANTHROPIC_API_KEY=sk-ant-...

# Required to call VSM order API
VSM_API_KEY=...
VSM_API_BASE=https://...

# Required to call the CRM comments API
CRM_API_BASE=https://...
```

### 3. Gmail access

**Option A — Claude Code session (no setup needed)**
The dashboard uses the built-in MCP Gmail bridge automatically when running inside a Claude Code session.

**Option B — Local / standalone**
One-time OAuth2 setup:
```bash
python gmail_sync.py --setup    # opens browser for Google sign-in
```
This saves `gmail_token.json` for subsequent runs.

### 4. Launch

```bash
bash run_dashboard.sh
```

Or manually:
```bash
streamlit run dashboard.py
```

Dashboard opens at **http://localhost:8501**

---

## Offline / sandbox mode

If you cannot reach the live VSM or CRM APIs, generate mock enriched data from the existing raw emails:

```bash
python generate_mock_data.py
```

This produces `ceo_escalation_emails_enriched.json` with realistic but synthetic VSM orders so the dashboard is fully functional without API access.

---

## File reference

| File | Purpose |
|---|---|
| `dashboard.py` | Main Streamlit app — overview charts, case list, order cards, RCA |
| `pipeline.py` | Orchestrates Gmail sync → body fetch → VSM enrichment in one call |
| `gmail_sync.py` | Fetches emails from Gmail (MCP or OAuth2) |
| `vsm_integration.py` | Calls VSM order API and CRM comments API; parses responses |
| `case_builder.py` | Groups emails into cases by thread ID and subject |
| `rca_engine.py` | Calls Claude Opus 4.6 to generate Root Cause Analysis per case |
| `generate_mock_data.py` | Creates synthetic enriched JSON for offline use |
| `store_state_mapping.csv` | 2,102 Lenskart store codes → city, state (used for zone lookup) |
| `ceo_escalation_emails.json` | Raw emails fetched from Gmail |
| `ceo_escalation_emails_enriched.json` | Emails + VSM orders + CRM comments (main data file) |
| `requirements.txt` | Python dependencies |
| `run_dashboard.sh` | One-command launcher |

---

## Zone detection

The dashboard assigns each case to a geographic zone (North / South / East / West) using this priority chain:

1. **Store code CSV** — matches `LKST\w+` code from VSM fields against `store_state_mapping.csv`
2. **VSM shipping address** — `shipping_state` / `shipping_city` from the order (online / home-delivery orders)
3. **VSM storeDetails** — `store_city` returned by the API
4. **Store name text** — regex extracts a city name from the store name string
5. **Email text scan** — searches subject, snippet, and body for known city names
6. **Omni email** — `@lenskartomni.com` customer address → `Store (Zone TBD)`

---

## Refreshing data

Click **"Sync & Enrich"** in the dashboard sidebar, or run the pipeline directly:

```bash
python pipeline.py
```

The pipeline prints a summary of new emails fetched and enriched.

---

## Requirements

- Python 3.11+
- `ANTHROPIC_API_KEY` (for RCA generation)
- VSM and CRM API credentials (for live order data)
- Gmail OAuth2 credentials or an active Claude Code session (for email sync)
