# Merchant Checkout Agent — Razorpay test mode

Makes a merchant (Afra Infra, a roofing/tiles business, used as the demo
merchant) **transactable end-to-end by an AI buyer**: an agent-readable
catalog, a policy-gated checkout agent, a bounded upsell agent, independent
LLM oversight, and a full audit trail — running against Razorpay's Order API
(mock by default, real test-mode SDK behind one env var).

## Why this shape

Agent-to-agent commerce (NPCI's UAP, ACP, AP2, x402) only works if a merchant
can let an *external* AI agent spend money on its behalf without a human
watching every step. That requires three things most demos skip:

1. **Explainable** — every decision (allow, block, offer, fail) is logged with
   the reason, not just the outcome.
2. **Bounded** — hard limits (per-order, per-session, per-line-qty, category
   allowlist, human-confirmation threshold) live in one file (`app/config.py`)
   and are enforced *before* any money action, never inferred by the LLM at
   call time.
3. **Gated** — the Razorpay call is the last step of the pipeline, not the
   first. Nothing reaches it without passing the policy engine.
4. **Oversight** — independent LLM auditor (Groq) provides a second opinion on
   transactions, catching patterns that rules-based systems might miss.

## Architecture

```
buyer / AI agent
      │
      ▼
GET /.well-known/agent-catalog.json   ← agent-readable catalog (app/catalog.py)
      │
      ▼
POST /agent/checkout                  ← app/agent.py: CheckoutAgent
      │
      ├─ 1. resolve cart against catalog
      ├─ 2. app/policy.py   → bounds check (audit: policy_check)
      ├─ 3. app/upsell.py   → ≤1 catalog-grounded upsell offer (audit: upsell_offer)
      ├─ 4. human-confirmation gate for high-value orders (audit: confirmation_required)
      ├─ 5. app/auditor.py  → independent LLM oversight via Groq (audit: llm_audit_review)
      ├─ 6. app/razorpay_client.py → create_order (mock or live)   (audit: razorpay_order_create / razorpay_failure)
      └─ 7. structured, buyer-facing result + remediation on failure

Every step writes to app/audit.py → data/audit_log.jsonl (append-only)
GET /audit and /audit.md expose it for inspection.
```

## API Documentation

### Interactive API Documentation
- **Swagger UI**: `http://127.0.0.1:8000/docs` — Interactive API exploration
- **ReDoc**: `http://127.0.0.1:8000/redoc` — Alternative API documentation

### Endpoints

#### `GET /.well-known/agent-catalog.json`
Returns the machine-readable catalog and policy manifest for AI agents.

**Response Structure:**
```json
{
  "merchant": {
    "id": "merchant_afra_infra_demo",
    "name": "Afra Infra Roofing & Tiles",
    "currency": "INR",
    "policy_url": "/.well-known/agent-catalog.json",
    "description": "..."
  },
  "products": [
    {
      "id": "sku_roof_sheet_std",
      "name": "Standard Galvanised Roofing Sheet (per unit)",
      "category": "roofing",
      "price_paise": 85000,
      "currency": "INR",
      "stock": 500,
      "description": "8ft galvanised iron roofing sheet, standard gauge.",
      "upsell_ids": ["sku_ridge_cap", "sku_installation_basic"]
    }
  ],
  "_agent_contract": {
    "checkout_endpoint": "/agent/checkout",
    "note": "Every checkout is policy-gated and logged..."
  }
}
```

#### `POST /agent/checkout`
Main checkout endpoint that processes orders through the policy engine.

**Request Body:**
```json
{
  "session_id": "session_abc123",
  "items": [
    {
      "product_id": "sku_roof_sheet_std",
      "qty": 8
    }
  ],
  "accept_upsell": true,
  "buyer_confirmed_high_value": false,
  "with_auditor": false,
  "customer_goal": "buy roofing sheets for small shed",
  "groq_api_key": ""
}
```

**Response Structure:**
```json
{
  "session_id": "session_abc123",
  "success": true,
  "message": "Order created: order_123 for INR 9300.00.",
  "cart": [
    {
      "id": "sku_roof_sheet_std",
      "name": "Standard Galvanised Roofing Sheet (per unit)",
      "qty": 8,
      "line_total_paise": 680000
    }
  ],
  "order_total_paise": 930000,
  "order": {
    "id": "order_123",
    "amount": 930000,
    "currency": "INR",
    "receipt": "rcpt_session_abc123_xyz"
  },
  "upsell_added": "sku_ridge_cap",
  "needs_confirmation": false,
  "audit_result": {
    "risk_flag": "clean",
    "reasoning": "Order matches stated goal and quantities are reasonable.",
    "model_used": "openai/gpt-oss-120b"
  },
  "remediation": null
}
```

#### `GET /audit?session_id={session_id}`
Returns the audit trail for a specific session or all sessions.

**Response Structure:**
```json
[
  {
    "entry_id": "abc123",
    "timestamp": "2024-01-15T10:30:00Z",
    "session_id": "session_abc123",
    "action_type": "cart_build",
    "summary": "Resolved 2/2 requested line(s); 0 unknown SKU(s).",
    "inputs": {"requested": [...]},
    "policy_checks": [],
    "outcome": "info",
    "explanation": ""
  }
]
```

#### `GET /audit.md?session_id={session_id}`
Returns the audit trail rendered as markdown.

## Run it

```bash
pip install -r requirements.txt --break-system-packages   # or use a venv
python demo.py                        # scripted 3-scenario run, prints the audit trail
uvicorn app.main:app --reload         # interactive server + demo UI at http://127.0.0.1:8000
```

The modern demo UI (`static/index.html`) features:
- **Customer-facing design**: Clean, modern interface that looks like a real e-commerce site
- **Product catalog**: Displays actual roofing/tile products with prices and descriptions
- **Smart goal input**: Natural language input for describing project needs
- **Live audit trail**: Real-time visualization of the checkout process
- **Policy vs auditor disagreement highlighting**: Visual alerts when the independent auditor flags something the policy engine allowed
- **Model attribution**: Clear labeling of which AI model (Gemini/Groq) performed each action

## The three scenarios `demo.py` proves

| # | Scenario | What happens | Razorpay called? |
|---|---|---|---|
| A | Normal purchase | Cart passes policy, one bounded upsell offered and accepted, order created | Yes — succeeds |
| B | Policy block | Requested qty (25) exceeds `MAX_LINE_QTY` (20) | **No** — blocked before any gateway call, reason logged |
| C | Gateway failure (the graceful-failure requirement) | Order amount clears merchant policy but exceeds the gateway's own ceiling | Yes — call made, gateway rejects it, agent catches `RazorpayError`, explains the *specific* cause, and offers a concrete remediation (split into multiple orders) — all logged, no silent retry, no funds moved |

## Independent LLM Auditor

The system includes an independent AI auditor powered by Groq that provides oversight on transactions:

- **Purpose**: Catches suspicious patterns that might technically pass policy checks but warrant human review
- **Model**: Uses `openai/gpt-oss-120b` (configurable via `GROQ_AUDITOR_MODEL` environment variable)
- **Triggers**: Flagged for things like unusual quantities, mismatched items, oddly large orders, or patterns suggesting automated abuse
- **Integration**: Runs after policy checks but before Razorpay calls, providing a second opinion without blocking transactions

**To enable the auditor:**
```bash
export GROQ_API_KEY=your_groq_api_key
```

Then enable it in the UI or set `with_auditor: true` in the API request.

## Switching from mock to real Razorpay test-mode

Mock mode needs zero credentials and is the default so this repo runs
anywhere. To point it at a real Razorpay **test** account:

```bash
export RAZORPAY_MODE=live
export RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
export RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxx
export RAZORPAY_WEBHOOK_SECRET=xxxxxxxxxxxxxxxx   # optional, for /webhook signature checks
```

Nothing else changes — `agent.py`, `policy.py`, `upsell.py` and `audit.py`
are identical in both modes; only `razorpay_client.py` branches on
`RAZORPAY_MODE`. This is deliberate: the guardrails must not depend on which
backend is behind them.

**Note on network access:** this sandbox's outbound network is restricted to
package registries (pypi, npm, github), so live-mode calls to
`api.razorpay.com` won't reach the internet from *this* environment — run
`RAZORPAY_MODE=live` locally or on a host with normal egress, using your own
Razorpay test-mode keys from the Dashboard.

## Where the bounds live

All money-relevant limits are in `app/config.py`, in one place, in plain
INR/paise, with a comment on why each exists:

- `MAX_SINGLE_ORDER_PAISE` — merchant's own per-order ceiling
- `MAX_SESSION_SPEND_PAISE` — caps a single buyer session across multiple checkouts
- `HUMAN_CONFIRM_ABOVE_PAISE` — orders at/above this need explicit confirmation, not auto-charge
- `MAX_LINE_QTY` — sanity bound per line item
- `ALLOWED_CATEGORIES` — category allowlist
- `MOCK_GATEWAY_CEILING_PAISE` — mirrors a real gateway's own transaction ceiling, used only by the mock backend

## Extending

- **Campaign orchestrator direction**: `upsell.py`'s catalog-grounded
  suggestion logic is the seed for a broader campaign agent — swap the
  single-candidate rule for a ranked list and add a `campaign_id` to the
  audit entries.
- **Conversational front-end**: `catalog.search()` is a placeholder keyword
  matcher; swap in an LLM-based intent parser that still resolves to real
  `product_id`s before calling `CheckoutAgent.checkout()` — the agent never
  trusts free text past that point.
- **Custom auditor models**: The Groq auditor model is configurable via the
  `GROQ_AUDITOR_MODEL` environment variable, making it easy to switch to newer
  models without code changes.