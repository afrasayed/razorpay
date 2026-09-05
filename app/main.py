"""
Endpoints:
  GET  /.well-known/agent-catalog.json  -> machine-readable catalog + policy pointers,
                                            what an external AI buyer agent fetches first.
  POST /agent/checkout                  -> run the CheckoutAgent for one request.
  GET  /audit                           -> full audit trail (JSON) for inspection.
  GET  /audit.md                        -> audit trail rendered as markdown.
  GET  /docs                            -> interactive API documentation (Swagger UI)
  GET  /                                -> modern customer-facing demo UI.
"""
import os
import uuid
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import catalog
from app import config
from app.agent import CheckoutAgent
from app.audit import AuditTrail
from app import inventory

app = FastAPI(
    title="Merchant Checkout Agent API",
    description="AI-powered checkout system with policy enforcement and independent oversight",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)
audit = AuditTrail()
agent = CheckoutAgent(audit)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


class CheckoutItem(BaseModel):
    product_id: str = Field(..., description="Product ID from the catalog")
    qty: int = Field(default=1, ge=1, description="Quantity (must be positive)")


class CheckoutRequest(BaseModel):
    session_id: str = Field(..., description="Unique session identifier")
    items: List[CheckoutItem] = Field(..., description="List of items to checkout")
    accept_upsell: bool = Field(default=True, description="Auto-accept upsell offers")
    buyer_confirmed_high_value: bool = Field(default=False, description="Buyer confirmed high-value order")
    with_auditor: bool = Field(default=False, description="Enable independent LLM auditor")
    customer_goal: str = Field(default="", description="Customer's shopping goal for auditor context")
    groq_api_key: str = Field(default="", description="Groq API key for independent auditor")
    gemini_api_key: str = Field(default="", description="Gemini API key for AI buyer agent")
    company: Optional[str] = Field(default=None, description="Target company or brand name")


class AuditResult(BaseModel):
    risk_flag: str = Field(..., description="Either 'clean' or 'flagged_for_review'")
    reasoning: str = Field(..., description="Natural language explanation of the assessment")
    model_used: str = Field(..., description="Which Groq model was used")


class CheckoutResponse(BaseModel):
    session_id: str = Field(..., description="Session identifier")
    success: bool = Field(..., description="Whether checkout succeeded")
    message: str = Field(..., description="Human-readable result message")
    cart: List[Dict[str, Any]] = Field(..., description="Final cart with resolved products")
    order_total_paise: int = Field(..., description="Total order amount in paise")
    order: Optional[Dict[str, Any]] = Field(None, description="Razorpay order details if successful")
    upsell_added: Optional[str] = Field(None, description="Product ID of added upsell, if any")
    needs_confirmation: bool = Field(default=False, description="Whether buyer confirmation is required")
    audit_result: Optional[AuditResult] = Field(None, description="Independent auditor result if enabled")
    remediation: Optional[str] = Field(None, description="Suggested remediation if checkout failed")


class CustomerOrderItem(BaseModel):
    sku: str = Field(..., description="Product SKU")
    qty: int = Field(default=1, ge=1, description="Quantity")


class CustomerOrderRequest(BaseModel):
    items: List[CustomerOrderItem] = Field(..., description="Items to order")
    notes: str = Field(default="", description="Order notes")
    auto_restock: bool = Field(default=True, description="Auto-trigger restock if stock drops below threshold")
    gemini_api_key: str = Field(default="", description="Gemini API key for auto-restock")
    groq_api_key: str = Field(default="", description="Groq API key for auto-restock auditor")
    company: Optional[str] = Field(default="Afra Infra", description="Company or brand name")


class GenerateCustomerOrderRequest(BaseModel):
    groq_api_key: str = Field(default="", description="Optional Groq API key for customer agent")
    company: Optional[str] = Field(default=None, description="Target company name")


class RestockApprovalRequest(BaseModel):
    order_id: int = Field(..., description="Restock order ID")
    action: str = Field(..., description="Either 'approve' or 'reject'")


class RestockCheckRequest(BaseModel):
    gemini_api_key: str = Field(default="", description="Gemini API key for AI buyer agent")
    groq_api_key: str = Field(default="", description="Groq API key for independent auditor")
    company: Optional[str] = Field(default="Afra Infra", description="Company or brand name")


class HeldOrderApprovalRequest(BaseModel):
    session_id: str = Field(..., description="Session ID of the held order")
    buyer_confirmed_high_value: bool = Field(default=False, description="Buyer confirmed high-value order")


@app.get("/companies")
def get_companies():
    """Get list of supported companies."""
    return catalog.get_companies()


@app.get("/.well-known/agent-catalog.json")
def agent_catalog(company: Optional[str] = None):
    manifest = catalog.get_manifest(company=company)
    manifest["_agent_contract"] = {
        "checkout_endpoint": "/agent/checkout",
        "note": "Every checkout is policy-gated and logged. Amounts above the merchant's "
                "human-confirmation threshold require buyer_confirmed_high_value=true.",
    }
    return manifest


@app.post("/agent/checkout", response_model=CheckoutResponse)
def checkout(req: CheckoutRequest) -> CheckoutResponse:
    result = agent.checkout(
        session_id=req.session_id,
        items=[i.model_dump() for i in req.items],
        accept_upsell=req.accept_upsell,
        buyer_confirmed_high_value=req.buyer_confirmed_high_value,
        with_auditor=req.with_auditor,
        customer_goal=req.customer_goal,
        groq_api_key=req.groq_api_key,
        gemini_api_key=req.gemini_api_key,
        company=req.company,
    )
    return result


@app.get("/audit")
def get_audit(session_id: str | None = None, company: str | None = None):
    return audit.for_session(session_id, company=company) if session_id else audit.all_entries(company=company)


@app.get("/audit.md")
def get_audit_md(session_id: str | None = None, company: str | None = None):
    entries = audit.for_session(session_id, company=company) if session_id else audit.all_entries(company=company)
    return Response(content=AuditTrail.render_markdown(entries), media_type="text/markdown")


# Inventory management endpoints
@app.get("/inventory")
def get_inventory_endpoint(company: Optional[str] = None):
    """Get current inventory levels."""
    return inventory.get_inventory(company=company)


@app.post("/inventory/reset")
def reset_inventory_endpoint(company: Optional[str] = None):
    """Reset inventory to initial seed levels."""
    inventory.reset_inventory(company=company)
    return {"success": True, "message": f"Inventory reset for {company or 'all companies'}"}



def _execute_single_restock(
    item: Dict,
    company: str,
    catalog_data: list,
    gemini_api_key: str = "",
    groq_api_key: str = "",
    trigger_source: str = "customer_order"
) -> Optional[Dict]:
    """
    Execute a single restock decision with:
    1. In-flight lock acquisition to strictly prevent duplicate concurrent restock attempts.
    2. SKU-appropriate quantity calculation (capital goods restock 1 unit; normal goods restock up to 2x threshold).
    3. Policy threshold routing (capital goods exceeding MAX_SINGLE_ORDER_PAISE are routed to pending approval by design).
    4. Autonomous checkout for normal goods passing policy limits.
    """
    sku = item["sku"]
    if not inventory.acquire_restock_lock(sku, company=company):
        return None
        
    try:
        goal = f"Restock {item['name']} (SKU: {sku}) for {company} - current stock: {item['quantity']}, threshold: {item['reorder_threshold']}. Triggered by {trigger_source}."
        prod_info = catalog.find_product(sku)
        unit_price = prod_info.get("price_paise", 0) if prod_info else 0
        is_capital_item = unit_price >= 5_000_000 or sku == "sku_industrial_bulk_order"
        
        if is_capital_item:
            # High-value capital asset (e.g. industrial bulk pallet at INR 1,55,000): restock exactly 1 unit
            restock_qty = 1
            cart_items = [{"product_id": sku, "qty": restock_qty}]
            mode = "capital_asset_rule"
            error_msg = None
        else:
            from app.agent import get_ai_buyer_cart
            cart_items, mode, error_msg = get_ai_buyer_cart(goal, catalog_data, gemini_api_key)
            if mode == "mock" or error_msg or not cart_items:
                restock_qty = max(item["reorder_threshold"] * 2 - item["quantity"], 10)
                cart_items = [{"product_id": sku, "qty": restock_qty}]
        
        if not cart_items:
            return None
            
        total_paise = 0
        for cart_item in cart_items:
            product = catalog.find_product(cart_item["product_id"])
            if product:
                total_paise += product["price_paise"] * cart_item["qty"]
        
        restock_session_id = f"restock_{sku}_{uuid.uuid4().hex[:8]}"
        
        # High-value items exceeding single order cap: held for human approval by design!
        if total_paise > config.MAX_SINGLE_ORDER_PAISE:
            order_id = inventory.create_restock_order(
                items=cart_items,
                total_paise=total_paise,
                ai_decision_context=f"High-value capital restock held for manual approval by design. Order total INR {total_paise/100:,.2f} exceeds auto cap INR {config.MAX_SINGLE_ORDER_PAISE/100:,.2f}.",
                company=company
            )
            reason = f"High-value capital order (INR {total_paise/100:,.2f} > INR {config.MAX_SINGLE_ORDER_PAISE/100:,.2f} cap). Held for human manager authorization."
            inventory.update_restock_order_status(
                order_id, "pending",
                auditor_verdict="flagged_for_review",
                auditor_reasoning=reason
            )
            audit.log(
                restock_session_id, "high_value_restock_hold",
                summary=f"[{company}] Restock for {sku} (INR {total_paise/100:,.2f}) held for human approval by design.",
                inputs={"cart": cart_items, "total_paise": total_paise, "company": company},
                outcome="blocked",
                explanation=reason,
                company=company
            )
            return {
                "sku": sku,
                "name": item["name"],
                "company": company,
                "current_stock": item["quantity"],
                "reorder_threshold": item["reorder_threshold"],
                "threshold": item["reorder_threshold"],
                "restock_qty": sum(ci.get("qty", 0) for ci in cart_items),
                "restock_items": cart_items,
                "total_paise": total_paise,
                "order_id": order_id,
                "session_id": restock_session_id,
                "ai_mode": mode,
                "ai_error": error_msg if error_msg else None,
                "checkout_result": {
                    "success": False,
                    "needs_approval": True,
                    "message": f"Held for human approval: INR {total_paise/100:,.2f} exceeds autonomous cap.",
                    "audit_result": {
                        "risk_flag": "flagged_for_review",
                        "reasoning": reason
                    }
                }
            }
            
        # Normal items within policy cap: proceed through autonomous checkout pipeline
        groq_key = groq_api_key or os.getenv("GROQ_API_KEY", "")
        gemini_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        checkout_result = agent.checkout(
            session_id=restock_session_id,
            items=cart_items,
            accept_upsell=False,
            buyer_confirmed_high_value=True,
            with_auditor=bool(groq_key),
            customer_goal=goal,
            groq_api_key=groq_key,
            gemini_api_key=gemini_key,
            company=company,
        )
        
        order_id = inventory.create_restock_order(
            items=cart_items,
            total_paise=total_paise,
            ai_decision_context=f"Restock for {company} via {trigger_source}. Mode: {mode}",
            company=company
        )
        
        if checkout_result.get("needs_approval"):
            inventory.update_restock_order_status(
                order_id, "pending",
                checkout_result.get("audit_result", {}).get("risk_flag"),
                checkout_result.get("audit_result", {}).get("reasoning")
            )
        elif checkout_result.get("success"):
            inventory.update_restock_order_status(order_id, "completed")
            for cart_item in cart_items:
                inventory.update_stock(cart_item["product_id"], cart_item["qty"], company=company)
        else:
            inventory.update_restock_order_status(order_id, "rejected")
            
        return {
            "sku": sku,
            "name": item["name"],
            "company": company,
            "current_stock": item["quantity"],
            "reorder_threshold": item["reorder_threshold"],
            "threshold": item["reorder_threshold"],
            "restock_qty": sum(ci.get("qty", 0) for ci in cart_items),
            "restock_items": cart_items,
            "total_paise": total_paise,
            "order_id": order_id,
            "session_id": restock_session_id,
            "ai_mode": mode,
            "ai_error": error_msg if error_msg else None,
            "checkout_result": {
                "success": checkout_result.get("success"),
                "message": checkout_result.get("message"),
                "needs_approval": checkout_result.get("needs_approval"),
                "audit_result": checkout_result.get("audit_result")
            }
        }
    finally:
        inventory.release_restock_lock(sku, company=company)


@app.post("/customer-order")
def process_customer_order_endpoint(req: CustomerOrderRequest):
    """Process a customer order (depletes inventory) and optionally auto-trigger restock."""
    items = [{"sku": item.sku, "qty": item.qty} for item in req.items]
    company = req.company or "Afra Infra"
    result = inventory.process_customer_order(items, req.notes, company=company)
    
    # Auto-trigger restock if enabled and order was successful
    auto_restock_result = None
    if result.get("success") and req.auto_restock:
        try:
            # 1. ONLY inspect items that were part of THIS customer order and whose post-order stock is at or below threshold
            ordered_skus = {item.sku for item in req.items}
            low_stock_items = []
            seen_skus = set()
            
            for processed_item in result.get("items", []):
                sku = processed_item.get("sku")
                if not sku or sku in seen_skus or sku not in ordered_skus:
                    continue
                seen_skus.add(sku)
                
                new_stock = processed_item.get("new_stock")
                threshold = processed_item.get("threshold", 10)
                if new_stock is not None and new_stock <= threshold:
                    # Deduplicate: check if there's already an in-flight, pending, or very recent restock order for this SKU
                    if not inventory.has_pending_or_recent_restock(sku, company=company, cooldown_seconds=60):
                        low_stock_items.append({
                            "sku": sku,
                            "name": processed_item.get("name", sku),
                            "quantity": new_stock,
                            "reorder_threshold": threshold,
                            "company": company
                        })
            
            if low_stock_items:
                auto_restock_session = f"auto_restock_{uuid.uuid4().hex[:8]}"
                audit.log(
                    auto_restock_session,
                    "auto_restock_check",
                    summary=f"[{company}] Auto-restock triggered by customer order. {len(low_stock_items)} item(s) below threshold.",
                    inputs={"customer_order_items": items, "low_stock_items": [item['sku'] for item in low_stock_items], "company": company},
                    outcome="info",
                    explanation="Customer order depleted stock below threshold, triggering automatic restock decision.",
                    company=company
                )
                
                catalog_data = catalog.get_manifest(company=company)
                restock_decisions = []
                
                for item in low_stock_items:
                    try:
                        decision = _execute_single_restock(
                            item=item,
                            company=company,
                            catalog_data=catalog_data,
                            gemini_api_key=req.gemini_api_key or "",
                            groq_api_key=req.groq_api_key or "",
                            trigger_source=f"customer order depletion for {company}"
                        )
                        if decision:
                            restock_decisions.append(decision)
                    except Exception as e:
                        restock_decisions.append({
                            "sku": item["sku"],
                            "name": item["name"],
                            "company": company,
                            "error": str(e)
                        })
                
                auto_restock_result = {
                    "triggered": True,
                    "company": company,
                    "items_below_threshold": len(low_stock_items),
                    "restock_decisions": restock_decisions,
                    "trigger_type": "auto_triggered_by_customer_order"
                }
                
                audit.log(
                    auto_restock_session,
                    "auto_restock_complete",
                    summary=f"[{company}] Auto-restock completed. {len([d for d in restock_decisions if 'checkout_result' in d])} restock decisions processed.",
                    inputs={"restock_decisions_count": len(restock_decisions), "company": company},
                    outcome="info",
                    explanation="Automatic restock decisions processed and inventory updated where applicable.",
                    company=company
                )
                
        except Exception as e:
            auto_restock_result = {
                "triggered": True,
                "company": company,
                "error": str(e),
                "trigger_type": "auto_triggered_by_customer_order"
            }
    
    # Add auto_restock_result to the response
    if auto_restock_result:
        result["auto_restock"] = auto_restock_result
    
    return result


@app.get("/customer-orders")
def get_customer_orders_endpoint(limit: int = 20, company: Optional[str] = None):
    """Get recent customer orders."""
    return inventory.get_customer_orders(limit=limit, company=company)


@app.api_route("/customer-order/generate", methods=["GET", "POST"])
def generate_customer_order_endpoint(req: Optional[GenerateCustomerOrderRequest] = None, groq_api_key: str = "", company: Optional[str] = None):
    """Generate a customer order using Groq as a lightweight customer agent."""
    req_company = (req.company if req else None) or company or "Afra Infra"
    inv_items = inventory.get_inventory(company=req_company)
    key = (req.groq_api_key if req else "") or groq_api_key or os.getenv("GROQ_API_KEY", "")
    from app import auditor
    return auditor.generate_customer_order(inv_items, groq_api_key=key, company=req_company)



@app.get("/restock-orders")
def get_restock_orders_endpoint(limit: int = 20, company: Optional[str] = None):
    """Get recent restock orders."""
    return inventory.get_restock_orders(limit=limit, company=company)


@app.get("/inventory/check")
def check_inventory_threshold(company: Optional[str] = None):
    """Check which items are below reorder threshold."""
    return inventory.get_items_below_threshold(company=company)


@app.post("/restock/approve")
def approve_restock_order_endpoint(req: RestockApprovalRequest):
    """Approve or reject a pending restock order."""
    if req.action == "approve":
        success = inventory.approve_restock_order(req.order_id)
        return {"success": success, "message": "Order approved" if success else "Failed to approve order"}
    elif req.action == "reject":
        success = inventory.reject_restock_order(req.order_id)
        return {"success": success, "message": "Order rejected" if success else "Failed to reject order"}
    else:
        return {"success": False, "message": "Invalid action. Use 'approve' or 'reject'"}


@app.post("/held-order/approve")
def approve_held_order_endpoint(req: HeldOrderApprovalRequest):
    """
    Approve a held order (flagged by auditor) and proceed to Razorpay.
    
    This endpoint processes a held order after human approval, 
    skipping back to the Razorpay call step.
    """
    # Get the audit trail to reconstruct the cart and audit result
    audit_entries = audit.for_session(req.session_id)
    
    # Find the cart_build entry to reconstruct the cart
    cart_build_entry = next((e for e in audit_entries if e["action_type"] == "cart_build"), None)
    if not cart_build_entry:
        return {"success": False, "message": "Could not find cart_build entry for this session"}
    
    # Find the audit result
    audit_entry = next((e for e in audit_entries if e["action_type"] == "llm_audit_review"), None)
    if not audit_entry:
        return {"success": False, "message": "Could not find audit review entry for this session"}
    
    # Reconstruct cart from audit inputs
    requested_items = cart_build_entry.get("inputs", {}).get("requested", [])
    cart = []
    for item in requested_items:
        product = catalog.find_product(item["product_id"])
        if product:
            qty = max(1, int(item.get("qty", 1)))
            cart.append({"product": product, "qty": qty, "line_total_paise": product["price_paise"] * qty})
    
    if not cart:
        return {"success": False, "message": "Could not reconstruct cart from audit trail"}
    
    # Reconstruct audit result
    audit_result = {
        "risk_flag": "flagged_for_review" if audit_entry["outcome"] == "flagged" else "clean",
        "reasoning": audit_entry.get("explanation", ""),
        "model_used": audit_entry.get("inputs", {}).get("model_used", "unknown")
    }
    
    # Process the held order
    result = agent.process_held_order(
        session_id=req.session_id,
        cart=cart,
        audit_result=audit_result,
        buyer_confirmed_high_value=req.buyer_confirmed_high_value
    )
    
    return result


@app.post("/restock/check")
def check_and_trigger_restock(req: RestockCheckRequest):
    """
    Check inventory for items below threshold and trigger AI restock decisions.
    
    This endpoint:
    1. Identifies items below reorder threshold
    2. Calls Gemini buyer agent to decide restock quantities
    3. Processes each restock decision through the full checkout pipeline
    4. Returns the decisions made
    """
    company = req.company or "Afra Infra"
    # Get items below threshold for company
    all_low = inventory.get_items_below_threshold(company=company)
    
    # Filter and deduplicate: skip items that already have an in-flight, pending, or recent restock order
    low_stock_items = []
    seen = set()
    for it in all_low:
        s = it["sku"]
        if s not in seen and not inventory.has_pending_or_recent_restock(s, company=company, cooldown_seconds=60):
            seen.add(s)
            low_stock_items.append(it)
    
    if not low_stock_items:
        return {
            "success": True,
            "company": company,
            "message": f"All items for {company} are above reorder threshold or have restock in progress. No restock needed.",
            "restock_decisions": [],
            "low_stock_items": []
        }
    
    restock_decisions = []
    catalog_data = catalog.get_manifest(company=company)
    
    for item in low_stock_items:
        try:
            decision = _execute_single_restock(
                item=item,
                company=company,
                catalog_data=catalog_data,
                gemini_api_key=req.gemini_api_key or "",
                groq_api_key=req.groq_api_key or "",
                trigger_source=f"scheduled/manual restock check for {company}"
            )
            if decision:
                restock_decisions.append(decision)
        except Exception as e:
            restock_decisions.append({
                "sku": item["sku"],
                "name": item["name"],
                "company": company,
                "error": str(e),
                "current_stock": item["quantity"],
                "threshold": item["reorder_threshold"]
            })
    
    return {
        "success": True,
        "company": company,
        "message": f"Processed {len(restock_decisions)} low-stock restock decisions for {company}",
        "low_stock_items": low_stock_items,
        "restock_decisions": restock_decisions
    }


if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
