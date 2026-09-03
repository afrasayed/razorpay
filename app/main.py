"""
Endpoints:
  GET  /.well-known/agent-catalog.json  -> machine-readable catalog + policy pointers,
                                            what an external AI buyer agent fetches first.
  POST /agent/checkout                  -> run the CheckoutAgent for one request.
  GET  /audit                           -> full audit trail (JSON) for inspection.
  GET  /audit.md                        -> audit trail rendered as markdown.
  GET  /                                -> minimal chat-style demo UI.
"""
import os
from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import catalog
from app.agent import CheckoutAgent
from app.audit import AuditTrail

app = FastAPI(title="Merchant Checkout Agent (Razorpay test mode)")
audit = AuditTrail()
agent = CheckoutAgent(audit)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


class CheckoutItem(BaseModel):
    product_id: str
    qty: int = 1


class CheckoutRequest(BaseModel):
    session_id: str
    items: list[CheckoutItem]
    accept_upsell: bool = True
    buyer_confirmed_high_value: bool = False
    with_auditor: bool = False
    customer_goal: str = ""
    groq_api_key: str = ""  # Pass GROQ API key from client to server


@app.get("/.well-known/agent-catalog.json")
def agent_catalog():
    manifest = catalog.get_manifest()
    manifest["_agent_contract"] = {
        "checkout_endpoint": "/agent/checkout",
        "note": "Every checkout is policy-gated and logged. Amounts above the merchant's "
                "human-confirmation threshold require buyer_confirmed_high_value=true.",
    }
    return manifest


@app.post("/agent/checkout")
def checkout(req: CheckoutRequest):
    result = agent.checkout(
        session_id=req.session_id,
        items=[i.model_dump() for i in req.items],
        accept_upsell=req.accept_upsell,
        buyer_confirmed_high_value=req.buyer_confirmed_high_value,
        with_auditor=req.with_auditor,
        customer_goal=req.customer_goal,
        groq_api_key=req.groq_api_key,
    )
    return result


@app.get("/audit")
def get_audit(session_id: str | None = None):
    return audit.for_session(session_id) if session_id else audit.all_entries()


@app.get("/audit.md")
def get_audit_md(session_id: str | None = None):
    entries = audit.for_session(session_id) if session_id else audit.all_entries()
    return Response(content=AuditTrail.render_markdown(entries), media_type="text/markdown")


if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
