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


class RestockApprovalRequest(BaseModel):
    order_id: int = Field(..., description="Restock order ID")
    action: str = Field(..., description="Either 'approve' or 'reject'")


class RestockCheckRequest(BaseModel):
    gemini_api_key: str = Field(default="", description="Gemini API key for AI buyer agent")
    groq_api_key: str = Field(default="", description="Groq API key for independent auditor")


class HeldOrderApprovalRequest(BaseModel):
    session_id: str = Field(..., description="Session ID of the held order")
    buyer_confirmed_high_value: bool = Field(default=False, description="Buyer confirmed high-value order")


@app.get("/.well-known/agent-catalog.json")
def agent_catalog():
    manifest = catalog.get_manifest()
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
    )
    return result


@app.get("/audit")
def get_audit(session_id: str | None = None):
    return audit.for_session(session_id) if session_id else audit.all_entries()


@app.get("/audit.md")
def get_audit_md(session_id: str | None = None):
    entries = audit.for_session(session_id) if session_id else audit.all_entries()
    return Response(content=AuditTrail.render_markdown(entries), media_type="text/markdown")


# Inventory management endpoints
@app.get("/inventory")
def get_inventory_endpoint():
    """Get current inventory levels."""
    return inventory.get_inventory()


@app.post("/customer-order")
def process_customer_order_endpoint(req: CustomerOrderRequest):
    """Process a customer order (depletes inventory) and optionally auto-trigger restock."""
    items = [{"sku": item.sku, "qty": item.qty} for item in req.items]
    result = inventory.process_customer_order(items, req.notes)
    
    # Auto-trigger restock if enabled and order was successful
    auto_restock_result = None
    if result.get("success") and req.auto_restock:
        try:
            # Check for items below threshold
            low_stock_items = inventory.get_items_below_threshold()
            
            if low_stock_items:
                # Log auto-trigger in audit trail
                auto_restock_session = f"auto_restock_{uuid.uuid4().hex[:8]}"
                audit.log(
                    auto_restock_session,
                    "auto_restock_check",
                    summary=f"Auto-restock triggered by customer order. {len(low_stock_items)} items below threshold.",
                    inputs={"customer_order_items": items, "low_stock_items": [item['sku'] for item in low_stock_items]},
                    outcome="info",
                    explanation="Customer order depleted stock below threshold, triggering automatic restock decision.",
                )
                
                # Trigger restock decisions
                catalog_data = catalog.get_manifest()
                restock_decisions = []
                
                for item in low_stock_items:
                    goal = f"Auto-restock {item['name']} (SKU: {item['sku']}) - current stock: {item['quantity']}, threshold: {item['reorder_threshold']}. Triggered by customer order depletion."
                    
                    try:
                        from app.agent import get_ai_buyer_cart
                        cart_items, mode, error_msg = get_ai_buyer_cart(goal, catalog_data, req.gemini_api_key)
                        
                        if mode == "mock" or error_msg:
                            restock_qty = max(item["reorder_threshold"] * 2 - item["quantity"], 10)
                            cart_items = [{"product_id": item["sku"], "qty": restock_qty}]
                        
                        if cart_items:
                            total_paise = 0
                            for cart_item in cart_items:
                                product = catalog.find_product(cart_item["product_id"])
                                if product:
                                    total_paise += product["price_paise"] * cart_item["qty"]
                            
                            # Process through checkout pipeline
                            restock_session_id = f"auto_restock_{item['sku']}_{uuid.uuid4().hex[:8]}"
                            checkout_result = agent.checkout(
                                session_id=restock_session_id,
                                items=cart_items,
                                accept_upsell=False,
                                buyer_confirmed_high_value=True,
                                with_auditor=bool(req.groq_api_key),
                                customer_goal=goal,
                                groq_api_key=req.groq_api_key,
                                gemini_api_key=req.gemini_api_key,
                            )
                            
                            # Create restock order record
                            order_id = inventory.create_restock_order(
                                items=cart_items,
                                total_paise=total_paise,
                                ai_decision_context=f"Auto-triggered by customer order depletion. Mode: {mode}"
                            )
                            
                            # Update restock order status
                            if checkout_result.get("needs_approval"):
                                inventory.update_restock_order_status(
                                    order_id, "pending",
                                    checkout_result.get("audit_result", {}).get("risk_flag"),
                                    checkout_result.get("audit_result", {}).get("reasoning")
                                )
                            elif checkout_result.get("success"):
                                inventory.update_restock_order_status(order_id, "completed")
                                for cart_item in cart_items:
                                    inventory.update_stock(cart_item["product_id"], cart_item["qty"])
                            else:
                                inventory.update_restock_order_status(order_id, "rejected")
                            
                            restock_decisions.append({
                                "sku": item["sku"],
                                "name": item["name"],
                                "current_stock": item["quantity"],
                                "reorder_threshold": item["reorder_threshold"],
                                "restock_qty": sum(ci.get("qty", 0) for ci in cart_items),
                                "restock_items": cart_items,
                                "total_paise": total_paise,
                                "order_id": order_id,
                                "session_id": restock_session_id,
                                "ai_mode": mode,
                                "checkout_result": {
                                    "success": checkout_result.get("success"),
                                    "needs_approval": checkout_result.get("needs_approval"),
                                    "audit_result": checkout_result.get("audit_result")
                                }
                            })
                            
                    except Exception as e:
                        restock_decisions.append({
                            "sku": item["sku"],
                            "name": item["name"],
                            "error": str(e)
                        })
                
                auto_restock_result = {
                    "triggered": True,
                    "items_below_threshold": len(low_stock_items),
                    "restock_decisions": restock_decisions,
                    "trigger_type": "auto_triggered_by_customer_order"
                }
                
                # Log auto-restock completion
                audit.log(
                    auto_restock_session,
                    "auto_restock_complete",
                    summary=f"Auto-restock completed. {len([d for d in restock_decisions if 'checkout_result' in d])} restock decisions processed.",
                    inputs={"restock_decisions_count": len(restock_decisions)},
                    outcome="info",
                    explanation="Automatic restock decisions processed and inventory updated where applicable.",
                )
                
        except Exception as e:
            auto_restock_result = {
                "triggered": True,
                "error": str(e),
                "trigger_type": "auto_triggered_by_customer_order"
            }
    
    # Add auto_restock_result to the response
    if auto_restock_result:
        result["auto_restock"] = auto_restock_result
    
    return result


@app.get("/customer-orders")
def get_customer_orders_endpoint(limit: int = 20):
    """Get recent customer orders."""
    return inventory.get_customer_orders(limit)


@app.get("/restock-orders")
def get_restock_orders_endpoint(limit: int = 20):
    """Get recent restock orders."""
    return inventory.get_restock_orders(limit)


@app.get("/inventory/check")
def check_inventory_threshold():
    """Check which items are below reorder threshold."""
    return inventory.get_items_below_threshold()


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
    # Get items below threshold
    low_stock_items = inventory.get_items_below_threshold()
    
    if not low_stock_items:
        return {
            "success": True,
            "message": "All items are above reorder threshold. No restock needed.",
            "restock_decisions": [],
            "low_stock_items": []
        }
    
    restock_decisions = []
    catalog_data = catalog.get_manifest()
    
    for item in low_stock_items:
        # Create a restock goal for the AI buyer
        goal = f"Restock {item['name']} (SKU: {item['sku']}) - current stock: {item['quantity']}, threshold: {item['reorder_threshold']}. Order a reasonable quantity to bring stock back to healthy levels."
        
        try:
            # Call the AI buyer agent (same logic as in agent.py)
            from app.agent import get_ai_buyer_cart
            cart_items, mode, error_msg = get_ai_buyer_cart(goal, catalog_data, req.gemini_api_key)
            
            if mode == "mock" or error_msg:
                # If mock mode or error, use a simple heuristic
                restock_qty = max(item["reorder_threshold"] * 2 - item["quantity"], 10)
                cart_items = [{"product_id": item["sku"], "qty": restock_qty}]
            
            if cart_items:
                # Calculate total
                total_paise = 0
                for cart_item in cart_items:
                    product = catalog.find_product(cart_item["product_id"])
                    if product:
                        total_paise += product["price_paise"] * cart_item["qty"]
                
                # Create a unique session ID for this restock
                restock_session_id = f"restock_{item['sku']}_{uuid.uuid4().hex[:8]}"
                
                # Process through the full checkout pipeline
                checkout_result = agent.checkout(
                    session_id=restock_session_id,
                    items=cart_items,
                    accept_upsell=False,  # No upsells for restock
                    buyer_confirmed_high_value=True,  # Auto-confirm for restock
                    with_auditor=bool(req.groq_api_key),  # Use auditor if key provided
                    customer_goal=goal,
                    groq_api_key=req.groq_api_key,
                    gemini_api_key=req.gemini_api_key,
                )
                
                # Create restock order record
                order_id = inventory.create_restock_order(
                    items=cart_items,
                    total_paise=total_paise,
                    ai_decision_context=f"AI decided to restock based on goal: {goal}. Mode: {mode}"
                )
                
                # Update restock order status based on checkout result
                if checkout_result.get("needs_approval"):
                    # Order was held by auditor
                    inventory.update_restock_order_status(
                        order_id, 
                        "pending",
                        checkout_result.get("audit_result", {}).get("risk_flag"),
                        checkout_result.get("audit_result", {}).get("reasoning")
                    )
                elif checkout_result.get("success"):
                    # Order completed successfully
                    inventory.update_restock_order_status(order_id, "completed")
                    # Update inventory
                    for cart_item in cart_items:
                        inventory.update_stock(cart_item["product_id"], cart_item["qty"])
                else:
                    # Order failed (policy, etc.)
                    inventory.update_restock_order_status(order_id, "rejected")
                
                restock_decisions.append({
                    "sku": item["sku"],
                    "name": item["name"],
                    "current_stock": item["quantity"],
                    "threshold": item["reorder_threshold"],
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
                })
                
        except Exception as e:
            restock_decisions.append({
                "sku": item["sku"],
                "name": item["name"],
                "error": str(e),
                "current_stock": item["quantity"],
                "threshold": item["reorder_threshold"]
            })
    
    return {
        "success": True,
        "message": f"Processed {len(low_stock_items)} low-stock items through checkout pipeline",
        "low_stock_items": low_stock_items,
        "restock_decisions": restock_decisions
    }


if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
