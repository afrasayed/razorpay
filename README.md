# Sentinel — Multi-Agent Autonomous Commerce & Oversight Engine

> **AI agents generate, evaluate, and independently audit every transaction — so no single AI's judgment goes unchecked.**

---

## Honest Status (as of submission)

| Component | Status |
|---|---|
| Groq Customer Agent (order generation) | LIVE — real API calls to Groq |
| Groq Independent Auditor | LIVE — real API calls to Groq |
| Gemini Restock Agent | LIVE — real API calls to Google Gemini |
| Razorpay Checkout | MOCK MODE — test order IDs, no real payment processing (RAZORPAY_MODE=mock) |
| Multi-company inventory isolation | LIVE — 5 businesses, fully tested, zero cross-company data leakage |
| Policy engine (spending caps, category rules) | LIVE — deterministic rule enforcement before every transaction |

---

## 1. Problem Statement

Most AI-driven commerce and checkout systems rely on a single AI's judgment with zero independent verification. If that AI hallucinates, gets manipulated, or makes a bad replenishment decision, nothing catches it before funds are committed and a payment transaction completes. High-trust autonomous commerce requires a strict division of powers: one agent to reason, deterministic rules to enforce business boundaries, and an independent third-party auditor to cross-check intent before execution.

---

## 2. Architecture Overview

Sentinel structures transaction execution across **5 decoupled infrastructure layers**:

1. **Customer Layer** (`Groq / Llama 3.3`): Simulates realistic B2B/B2C customer order demands and inventory depletions.
2. **Policy Layer** (`Deterministic Rule Engine`): Enforces non-negotiable financial caps (order limits, session spend, quantity ceilings, category allowlists) before any money API is touched.
3. **Reasoning Layer** (`Google Gemini`): Autonomous restock decision engine that evaluates stock deficits against lead times and formulates replenishment orders.
4. **Audit Layer** (`Groq Independent Auditor`): An isolated LLM auditor running independently from the reasoning model to detect goal-cart mismatches, quantity spikes, and unusual spend patterns.
5. **Ledger Layer** (`Immutable Audit Trail`): Append-only event log capturing every prompt, policy evaluation, auditor verdict, and payment transaction.

### Transaction Flow
```text
Customer Order
      │
      ▼
Policy Check (Hard Rules) ──[Blocked]──► Order Rejected
      │
      ▼ [Passed]
Stock Depletion
      │
      ▼
Restock Trigger (Google Gemini Autonomous Buyer)
      │
      ▼
Independent Audit (Groq Oversight Layer)
      │
      ├──[Flagged / Spike Detected]──► Held for Human Operator Approval
      │                                       │
      │                                 [Override]
      ▼ [Approved]                            │
Razorpay Checkout ◄───────────────────────────┘
      │
      ▼
Immutable Audit Log & Command Centre Feed
```

---

## 3. Key Features

- **Multi-Agent Verification**: Policy boundaries and the LLM safety auditor operate completely independently. Disagreements and security holds are transparently surfaced to the operator dashboard, never swept under the rug.
- **Multi-Tenant Isolation**: Complete data and inventory isolation across **5 distinct business domains**:
  - **Afra Infra**: Smart Roofing & Industrial Building Supplies
  - **Tropicana**: Cold-Chain Juices & Premium Beverages
  - **Amul**: Perishable Dairy & Pantry Essentials
  - **Minimalist**: Active Dermatology & Clinical Skincare
  - **Nestle**: Packaged Foods & Confectionery
- **Live Simulation Mode**: Single-click autonomous simulation loops triggering real-time customer demand, threshold breaches, automated restock calculations, and independent audits.
- **Command Centre & Audit Trail**: Real-time side-by-side feed of customer orders, restock events, policy evaluations, and payment references with an explicit operator override workflow.

---

## 4. Tech Stack

- **Backend**: Python 3.12, FastAPI, Uvicorn
- **Customer Generation & Independent Auditor**: Groq Cloud (`Llama 3.3 70B` / `openai/gpt-oss-120b`)
- **Autonomous Restock Reasoning**: Google Gemini (`gemini-flash-latest` / `gemini-3.6-flash`)
- **Payments Gateway**: Razorpay Orders API (Mock mode enabled by default; zero external credentials required for local demo)
- **Persistence & State**: SQLite (`data/inventory.db`) with JSONL append-only audit trail (`data/audit_log.jsonl`)
- **Frontend Dashboard**: Responsive Vanilla HTML5/CSS3/ES6 (Zero heavy dependencies, dark technical UI)

---

## 5. Setup & Running Locally

### 1. Clone & Install
```bash
git clone https://github.com/afrasayed/razorpay.git
cd razorpay
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Set the following variables in `.env`:
```env
# Required for Independent Auditor & Customer Agent
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_AUDITOR_MODEL=llama-3.3-70b-versatile

# Required for Autonomous Restock Buyer
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-flash-latest

# Payment Mode ("mock" runs full loop offline without Razorpay account)
RAZORPAY_MODE=mock
```

### 3. Launch Application
```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
Open **`http://127.0.0.1:8000`** in your browser. Interactive Swagger API docs are available at **`/docs`**.

---

## 6. What Broke & How It Was Fixed

Building a multi-agent autonomous system surfaces complex integration failure modes. Here is how we engineered reliability into the system:

- **Silent Auditor Fail-Open Contradiction**: An early bug allowed failed/errored auditor calls (e.g. invalid API keys or timeouts) to fall through to a clean state. We re-engineered the auditor pipeline with explicit tri-state error handling (`✓ APPROVED`, `⚠ FLAGGED`, `✗ AUDIT FAILED`) so system errors never mask as approvals.
- **GitHub Push Protection on API Keys**: A test API key was flagged during a pre-commit push. We migrated all credentials strictly to `.env` loaders and implemented automated scrubbing across test fixtures.
- **Restock Policy Cap Recalibration**: Initial single-order limits were capped at ₹10,000, causing 100% of standard B2B restock batches (typically ₹14,000–₹25,000) to be blocked by policy. We recalibrated policy thresholds to align with B2B wholesale replenishment while keeping guardrails tight.
- **Gemini Key & Model Deprecation Handling**: Google Cloud tokens (`AQ.Ab8...`) and decommissioned model names caused silent 401/404 fallbacks. We fixed the configuration to use `gemini-flash-latest` with automatic cascading fallbacks across `["gemini-flash-latest", "gemini-3.6-flash", "gemini-2.0-flash"]`.

---

## 7. Future Work

- **Autonomous Agent-to-Agent Price & Volume Negotiation**: Extending the customer-merchant boundary from static ordering to dynamic agentic bargaining, where the buyer agent negotiates bulk tier discounts against merchant volume policies before committing to checkout.