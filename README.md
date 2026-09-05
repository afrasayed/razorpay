# Sentinel — Multi-Agent Autonomous Commerce Engine

AI agents generate, evaluate, and independently audit every transaction — so no single AI's judgment goes unchecked.

## Honest Status
| Component | Status |
|---|---|
| Groq Customer Agent | LIVE |
| Groq Independent Auditor | LIVE |
| Gemini Restock Agent | LIVE |
| Razorpay Checkout | MOCK MODE (test order IDs) |
| Multi-company isolation | LIVE — 5 businesses, zero data leakage |
| Policy engine | LIVE — deterministic rule enforcement |

## Problem
AI-driven commerce systems typically trust a single AI's judgment with no independent verification — a hallucination or bad decision goes unchecked straight to checkout. Sentinel separates reasoning, rule enforcement, and auditing into independent layers.

## Architecture
Customer Order → Policy Check → Stock Depletion → Gemini Restock → Groq Audit → Checkout → Audit Log


- **Customer Layer** (Groq/Llama 3.3) — generates realistic orders
- **Policy Layer** — enforces spend caps, quantity limits, category rules
- **Reasoning Layer** (Gemini) — autonomous restock decisions
- **Audit Layer** (Groq) — independent auditor flags anomalies, separate from policy
- **Ledger Layer** — full append-only audit trail

## Key Features
- **Independent verification** — policy and auditor disagree openly; nothing's hidden
- **Multi-tenant** — 5 isolated businesses (Afra Infra, Tropicana, Amul, Minimalist, Nestle)
- **Live simulation mode** — autonomous order/restock loop for demos
- **Command Centre** — full order + restock + audit history in one view

## Tech Stack
FastAPI · Groq (Llama 3.3) · Google Gemini · Razorpay (mock) · SQLite

## Setup
```bash
git clone https://github.com/afrasayed/razorpay.git
cd razorpay
pip install -r requirements.txt
cp .env.example .env
# add GROQ_API_KEY, GEMINI_API_KEY, RAZORPAY_MODE=mock
uvicorn app.main:app --reload --port 8000
```

## What Broke & How It Was Fixed
- **Silent auditor fail-open** — a failed audit call used to display as "approved." Fixed with explicit tri-state handling (Approved / Flagged / Failed).
- **Leaked API key** — caught by GitHub push protection; migrated all secrets to `.env`.
- **Policy caps too low** — restock orders were universally rejected; recalibrated limits for realistic B2B order sizes.
- **Invalid Gemini key format** — an OAuth token was mistakenly used instead of an API key; fixed and added model fallbacks.

## Future Work
Agent-to-agent negotiation — letting the customer and merchant AI bargain on price/volume directly.