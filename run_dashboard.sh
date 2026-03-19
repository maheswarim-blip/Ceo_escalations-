#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# CEO Escalation Dashboard — quick-start script
# ──────────────────────────────────────────────────────────────────────────────

set -e
cd "$(dirname "$0")"

# 1. Install dependencies
echo "📦 Installing dependencies..."
pip install -q -r requirements.txt

# 2. Generate enriched data if it doesn't exist
if [ ! -f "ceo_escalation_emails_enriched.json" ]; then
    echo "⚙️  Generating mock enriched data (VSM + CRM)..."
    python3 generate_mock_data.py
fi

# 3. Check ANTHROPIC_API_KEY
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    echo "⚠️  ANTHROPIC_API_KEY is not set."
    echo "   Export it to enable AI-powered RCA:"
    echo "   export ANTHROPIC_API_KEY=sk-ant-..."
    echo ""
fi

# 4. Launch dashboard
echo "🚀 Starting dashboard at http://localhost:8501"
streamlit run dashboard.py \
    --server.port 8501 \
    --server.headless true \
    --browser.gatherUsageStats false
