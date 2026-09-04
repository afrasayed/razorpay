"""
Central config. Every value here is either an explicit bound the agent is
gated by, or a switch between mock/live Razorpay mode. Nothing money-related
is hardcoded anywhere else in the codebase -- change limits here only.
"""
import os

# --- Razorpay mode ---
# "mock"  -> in-memory simulated Razorpay responses, no network needed. Default,
#            so this repo runs end to end with zero credentials.
# "live"  -> real Razorpay Python SDK against **test-mode** keys
#            (rzp_test_... / test secret). Never point this at live keys from
#            an autonomous agent without a human-in-the-loop payment step.
RAZORPAY_MODE = os.getenv("RAZORPAY_MODE", "mock")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

# --- Money bounds the agent is gated by (all amounts in paise) ---
# Razorpay's own documented per-transaction ceiling for standard test/live
# checkout is far higher than this; we deliberately set a *tighter* merchant
# policy ceiling so the agent's own gate -- not the gateway's -- is what
# normally stops runaway carts. The one SKU priced above this in catalog.json
# exists to demonstrate the gateway-level failure path too.
MAX_SINGLE_ORDER_PAISE = 1_000_000          # INR 10,000 per order
MAX_SESSION_SPEND_PAISE = 2_000_000         # INR 20,000 total per buyer session
HUMAN_CONFIRM_ABOVE_PAISE = 500_000         # INR 5,000+ needs explicit buyer confirmation
MAX_LINE_QTY = 20                           # no single line item over this qty

ALLOWED_CATEGORIES = {"roofing", "tiles", "accessory", "service"}

# Simulated gateway ceiling used only by the mock client, to mirror the fact
# that real payment gateways reject absurdly large single transactions.
MOCK_GATEWAY_CEILING_PAISE = 5_000_000      # INR 50,000

AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "audit_log.jsonl"
))

# --- AI Buyer Model Configuration ---
# Model to use for AI buyer agent (Gemini API)
# Default to a stable Flash-tier model that's fast and cost-effective
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
