# CEO Escalation Dashboard — Business Overview

**Document type:** Internal business brief
**Audience:** Operations, CX leadership, product owners
**Last updated:** March 2026

---

## What is it?

The CEO Escalation Dashboard is an internal tool that centralises, enriches, and analyses customer complaints that are escalated directly to the CEO inbox (`ceoescalation@lenskart.com`).

When a customer escalates a complaint to the CEO, the email arrives in Gmail. The dashboard automatically pulls those emails, matches them to live order data from VSM (our order management system) and CRM, groups related emails into a single case, and presents everything in one place so the operations and CX team can investigate and resolve it quickly.

It also uses Claude (Anthropic's AI) to generate a draft Root Cause Analysis for each escalation — giving the team a structured first-pass analysis within seconds.

---

## Why it exists

CEO escalations represent the most frustrated customers. Historically, handling them required manually searching across Gmail, VSM, and CRM to piece together what happened and who owns the resolution. This tool eliminates that context-switching and gives every team member the same complete picture of each case from a single screen.

---

## Key features

### 1. Automatic email ingestion
Emails to `ceoescalation@lenskart.com` are pulled automatically from Gmail. The system runs incrementally — only new emails are processed each time, so there is no risk of duplicated work.

### 2. VSM order enrichment
Each email is matched to real order data from VSM. For every order the dashboard shows:
- Order status, tracking checkpoint, and status history
- Customer name, phone, tier (Gold, etc.)
- Store name, city, and state (or shipping address for online orders)
- Order value, items purchased, payment status
- Whether the order is cancellable or returnable

### 3. CRM / WhatsApp conversation history
WhatsApp and CRM comments linked to each order are displayed alongside the email thread, giving a complete view of prior customer interactions without leaving the dashboard.

### 4. Case grouping
Related emails (same thread, same subject, same order) are automatically grouped into a single case so the team works on a case — not on individual emails. Each case shows total email count, all linked orders, and the full conversation thread.

### 5. Geographic zone analysis
Each case is assigned to a business zone (North / South / East / West) using a priority lookup:
- Store code matched against a master list of 2,100+ Lenskart stores
- Shipping address for online / home-delivery orders
- Store city from VSM, store name text, or email content as fallbacks

This powers the zone breakdown chart showing where escalations are concentrated.

### 6. Overview analytics
The Overview tab provides a real-time snapshot across all cases:
- **Total cases, High / Medium / Low severity counts**
- **Escalations by zone** — which region is generating the most complaints
- **Escalations by issue type** — e.g. delivery delay, wrong product, refund pending
- **Weekly and monthly trend charts** — volume over time
- **Full-text search** across all case titles and snippets

### 7. Severity and issue classification
Each case is classified by severity (High / Medium / Low) and issue type (delivery, refund, product quality, etc.). The sidebar lets the team filter by severity and issue type to prioritise the most critical cases.

### 8. AI-powered Root Cause Analysis (RCA)
With one click, Claude Opus 4.6 analyses the original escalation email and its matched VSM order data to generate a structured RCA covering:
- Root cause (confirmed or hypothesis)
- Customer impact
- Likely responsible team
- Recommended next actions
- Data gaps that would change the conclusion

The RCA is generated with extended thinking enabled, so it reasons through the case before producing the output.

### 9. Voice of Customer (VoC) analysis
A batch analysis across all escalation cases that identifies recurring complaint themes, customer pain points, and operational gaps — written as a management-level report powered by Claude.

---

## Current limitations

| Area | Limitation |
|---|---|
| **Zone coverage** | ~16 cases resolve as "Unknown" zone. These include international stores (Singapore, Jeddah), franchise codes (ST2xx, ST3xx) not in the master store list, and home-delivery hub codes (LKH prefix) which have no geographic mapping |
| **Shipping address** | Online order shipping addresses are not present in the current enriched data — the VSM API field was only recently added to the extraction pipeline. Re-enrichment is needed to populate this for existing cases |
| **Store code gaps** | The store master CSV covers 2,102 stores but does not include all franchise partners (ST codes) or home-delivery hubs (LKH codes) |
| **No assignment workflow** | The dashboard is read-only. There is no way to assign a case to a team member, set a resolution status, or add internal notes from within the tool |
| **No SLA tracking** | The system captures escalation date but does not track resolution time, breach of SLA, or first-response time |
| **Manual RCA trigger** | RCA must be generated per case on demand. There is no automatic generation when a new escalation arrives |
| **Gmail sync is pull-based** | Data refreshes when the user clicks "Sync & Enrich" or when the pipeline is run manually. There is no real-time push from Gmail |
| **No alerting** | High-severity cases are visible in the dashboard but do not trigger any notification (Slack, email, etc.) to the responsible team |
| **International orders** | Stores outside India (currently Singapore and Middle East codes visible in data) are not mapped to any zone and show limited order detail |

---

## Future roadmap

### Near-term
- **Fill store code gaps** — add missing ST and LKH codes with their state/city to the store master CSV so zone coverage reaches 100%
- **Re-enrich historical data** — run the updated pipeline on existing cases to backfill shipping address and zone for online orders
- **Auto-RCA on new cases** — trigger RCA generation automatically when a new email arrives, so the team sees analysis immediately on opening a case

### Medium-term
- **Case assignment and status** — add the ability to assign a case to an owner, mark it as In Progress / Resolved, and leave internal resolution notes
- **SLA tracking** — calculate time-to-first-response and time-to-resolution per case; flag breached SLAs in red on the dashboard
- **Slack / email alerts** — notify the relevant zone ops lead when a High-severity case arrives
- **Trend anomaly detection** — automatically flag when escalation volume in a zone spikes above its normal baseline

### Longer-term
- **CRM write-back** — push resolution status and RCA summary back into the CRM system so it stays in sync without manual entry
- **Predictive risk scoring** — use order attributes (delivery delay, item type, customer tier) to score incoming orders for escalation risk before the customer emails the CEO
- **Executive reporting** — weekly automated summary email to leadership with top issues, zone breakdown, and resolution rates
- **Multi-inbox support** — extend beyond `ceoescalation@lenskart.com` to cover other escalation channels (social media DMs, app store reviews, etc.)

---

## Who uses it

| Role | How they use it |
|---|---|
| CX Operations | Triage and investigate individual escalation cases |
| Zone / Regional Managers | Monitor escalation volume and issue types in their zone |
| CX Leadership | Track severity trends, identify recurring operational failures |
| Product / Process teams | Use VoC analysis to identify systemic gaps |
