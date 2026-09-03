# Merchant Checkout Agent — Razorpay test mode

Makes a merchant (Afra Infra, a roofing/tiles business, used as the demo
merchant) **transactable end-to-end by an AI buyer**: an agent-readable
catalog, a policy-gated checkout agent, a bounded upsell agent, and a full
audit trail — running against Razorpay's Order API (mock by default, real
test-mode SDK behind one env var).

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
      ├─ 5. app/razorpay_client.py → create_order (mock or live)   (audit: razorpay_order_create / razorpay_failure)
      └─ 6. structured, buyer-facing result + remediation on failure

Every step writes to app/audit.py → data/audit_log.jsonl (append-only)
GET /audit and /audit.md expose it for inspection.
```

## Run it

```bash
pip install -r requirements.txt --break-system-packages   # or use a venv
python demo.py                        # scripted 3-scenario run, prints the audit trail
uvicorn app.main:app --reload         # interactive server + demo UI at http://127.0.0.1:8000
```

The demo UI (`static/index.html`) lets you build a cart, run checkout as an
AI buyer, and watch the live audit trail update per session.

## The three scenarios `demo.py` proves

| # | Scenario | What happens | Razorpay called? |
|---|---|---|---|
| A | Normal purchase | Cart passes policy, one bounded upsell offered and accepted, order created | Yes — succeeds |
| B | Policy block | Requested qty (25) exceeds `MAX_LINE_QTY` (20) | **No** — blocked before any gateway call, reason logged |
| C | Gateway failure (the graceful-failure requirement) | Order amount clears merchant policy but exceeds the gateway's own ceiling | Yes — call made, gateway rejects it, agent catches `RazorpayError`, explains the *specific* cause, and offers a concrete remediation (split into multiple orders) — all logged, no silent retry, no funds moved |

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
