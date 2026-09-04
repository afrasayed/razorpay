"""
Inventory management using SQLite database.

This module handles:
- Stock level tracking for SKUs
- Customer order processing (depleting stock)
- Restock operations (increasing stock)
- Reorder threshold checking
"""
import sqlite3
import os
from typing import List, Dict, Optional
from datetime import datetime

# Database path
_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "inventory.db")

# Ensure data directory exists
os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)


def _get_connection():
    """Get SQLite connection with row factory for dict access."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """Initialize inventory database with schema and seed data."""
    conn = _get_connection()
    cursor = conn.cursor()
    
    # Create inventory table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            sku TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            reorder_threshold INTEGER NOT NULL DEFAULT 10,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create customer orders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customer_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            items TEXT NOT NULL,  -- JSON array of {sku, qty}
            total_paise INTEGER NOT NULL,
            notes TEXT
        )
    """)
    
    # Create restock orders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS restock_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            items TEXT NOT NULL,  -- JSON array of {sku, qty}
            total_paise INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending, approved, rejected, completed
            auditor_verdict TEXT,  -- clean, flagged_for_review
            auditor_reasoning TEXT,
            ai_decision_context TEXT,  -- Gemini's reasoning
            human_approval_date TIMESTAMP,
            razorpay_order_id TEXT
        )
    """)
    
    conn.commit()
    
    # Seed inventory if empty
    cursor.execute("SELECT COUNT(*) as count FROM inventory")
    if cursor.fetchone()["count"] == 0:
        _seed_inventory(cursor)
        conn.commit()
    
    conn.close()


def _seed_inventory(cursor):
    """Seed inventory with realistic starting stock levels from catalog."""
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    import catalog
    
    seed_data = [
        # Roofing sheets - moderate stock, low threshold
        ("sku_roof_sheet_std", "Standard Galvanised Roofing Sheet", 50, 15),
        ("sku_roof_sheet_premium", "Premium Coated Roofing Sheet", 30, 10),
        
        # Tiles - good stock, moderate threshold  
        ("sku_clay_tile", "Terracotta Clay Roof Tile", 100, 20),
        
        # Accessories - good stock, low threshold
        ("sku_ridge_cap", "Ridge Cap Set", 40, 10),
        ("sku_tile_sealant", "Tile Sealant (5L)", 50, 15),
        
        # Services - unlimited (high threshold to never trigger)
        ("sku_installation_basic", "Basic Installation Labour", 9999, 1000),
        ("sku_installation_premium", "Premium Installation + Site Inspection", 9999, 1000),
        
        # Bulk - very low stock, low threshold (for demo)
        ("sku_industrial_bulk_order", "Industrial Bulk Sheet Pallet", 2, 1),
    ]
    
    for sku, name, quantity, threshold in seed_data:
        cursor.execute(
            "INSERT INTO inventory (sku, name, quantity, reorder_threshold) VALUES (?, ?, ?, ?)",
            (sku, name, quantity, threshold)
        )


def get_inventory() -> List[Dict]:
    """Get current inventory levels."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory ORDER BY sku")
    items = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return items


def get_item_stock(sku: str) -> Optional[Dict]:
    """Get stock level for a specific SKU."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory WHERE sku = ?", (sku,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def process_customer_order(items: List[Dict], notes: str = "") -> Dict:
    """
    Process a customer order - deplete inventory.
    
    Args:
        items: List of {"sku": str, "qty": int}
        notes: Optional notes about the order
    
    Returns:
        Dict with success status, message, and order details
    """
    import json
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    import catalog as catalog_module
    
    conn = _get_connection()
    cursor = conn.cursor()
    
    total_paise = 0
    processed_items = []
    insufficient_stock = []
    
    try:
        # Calculate total and check stock
        for item in items:
            sku = item["sku"]
            qty = item["qty"]
            
            # Get product info from catalog for pricing
            product = catalog_module.find_product(sku)
            if not product:
                insufficient_stock.append(f"{sku} - unknown product")
                continue
            
            # Check current stock
            cursor.execute("SELECT quantity, reorder_threshold FROM inventory WHERE sku = ?", (sku,))
            row = cursor.fetchone()
            if not row:
                insufficient_stock.append(f"{sku} - not in inventory")
                continue
            
            current_stock = row["quantity"]
            reorder_threshold = row["reorder_threshold"]
            if current_stock < qty:
                insufficient_stock.append(f"{sku} - only {current_stock} available, need {qty}")
                continue
            
            # Stock is sufficient, add to processed
            total_paise += product["price_paise"] * qty
            new_stock = current_stock - qty
            processed_items.append({
                "sku": sku,
                "qty": qty,
                "name": product["name"],
                "new_stock": new_stock,
                "threshold": reorder_threshold
            })
        
        if insufficient_stock:
            conn.close()
            return {
                "success": False,
                "message": f"Insufficient stock for: {', '.join(insufficient_stock)}",
                "items": processed_items,
                "total_paise": 0
            }
        
        if not processed_items:
            conn.close()
            return {
                "success": False,
                "message": "No valid items to process",
                "items": [],
                "total_paise": 0
            }
        
        # Deplete stock
        for item in processed_items:
            cursor.execute(
                "UPDATE inventory SET quantity = quantity - ?, last_updated = CURRENT_TIMESTAMP WHERE sku = ?",
                (item["qty"], item["sku"])
            )
        
        # Log customer order
        cursor.execute(
            """INSERT INTO customer_orders (items, total_paise, notes) 
               VALUES (?, ?, ?)""",
            (json.dumps(processed_items), total_paise, notes)
        )
        
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "message": f"Order processed successfully. Total: INR {total_paise/100:.2f}",
            "items": processed_items,
            "total_paise": total_paise
        }
        
    except Exception as e:
        conn.rollback()
        conn.close()
        return {
            "success": False,
            "message": f"Error processing order: {str(e)}",
            "items": [],
            "total_paise": 0
        }


def get_items_below_threshold() -> List[Dict]:
    """Get items that are below their reorder threshold."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM inventory 
        WHERE quantity <= reorder_threshold 
        ORDER BY quantity ASC
    """)
    items = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return items


def update_stock(sku: str, quantity_change: int) -> bool:
    """
    Update stock level for a SKU (positive to add, negative to remove).
    
    Returns:
        True if successful, False otherwise
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "UPDATE inventory SET quantity = quantity + ?, last_updated = CURRENT_TIMESTAMP WHERE sku = ?",
            (quantity_change, sku)
        )
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    except Exception as e:
        conn.rollback()
        conn.close()
        return False


def get_customer_orders(limit: int = 20) -> List[Dict]:
    """Get recent customer orders."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM customer_orders 
        ORDER BY order_date DESC 
        LIMIT ?
    """, (limit,))
    orders = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return orders


def get_restock_orders(limit: int = 20) -> List[Dict]:
    """Get recent restock orders."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM restock_orders 
        ORDER BY order_date DESC 
        LIMIT ?
    """, (limit,))
    orders = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return orders


def create_restock_order(items: List[Dict], total_paise: int, 
                        ai_decision_context: str = "") -> int:
    """
    Create a pending restock order.
    
    Returns:
        The order ID
    """
    import json
    
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        """INSERT INTO restock_orders (items, total_paise, status, ai_decision_context)
           VALUES (?, ?, 'pending', ?)""",
        (json.dumps(items), total_paise, ai_decision_context)
    )
    
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return order_id


def update_restock_order_status(order_id: int, status: str, 
                                auditor_verdict: str = None,
                                auditor_reasoning: str = None) -> bool:
    """
    Update restock order status and auditor verdict.
    
    Status options: pending, approved, rejected, completed
    Auditor verdict: clean, flagged_for_review
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    try:
        if auditor_verdict:
            cursor.execute(
                """UPDATE restock_orders 
                   SET status = ?, auditor_verdict = ?, auditor_reasoning = ?
                   WHERE id = ?""",
                (status, auditor_verdict, auditor_reasoning, order_id)
            )
        else:
            cursor.execute(
                "UPDATE restock_orders SET status = ? WHERE id = ?",
                (status, order_id)
            )
        
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    except Exception as e:
        conn.rollback()
        conn.close()
        return False


def approve_restock_order(order_id: int) -> bool:
    """Approve a pending restock order."""
    conn = _get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """UPDATE restock_orders 
               SET status = 'approved', human_approval_date = CURRENT_TIMESTAMP
               WHERE id = ? AND status = 'pending'"""
        )
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    except Exception as e:
        conn.rollback()
        conn.close()
        return False


def complete_restock_order(order_id: int, razorpay_order_id: str = None) -> bool:
    """
    Mark a restock order as completed and update inventory.
    
    This should be called after successful Razorpay payment.
    """
    import json
    
    conn = _get_connection()
    cursor = conn.cursor()
    
    try:
        # Get order details
        cursor.execute("SELECT items, status FROM restock_orders WHERE id = ?", (order_id,))
        row = cursor.fetchone()
        
        if not row or row["status"] not in ["approved", "pending"]:
            conn.close()
            return False
        
        items = json.loads(row["items"])
        
        # Update inventory
        for item in items:
            update_stock(item["sku"], item["qty"])
        
        # Mark as completed
        if razorpay_order_id:
            cursor.execute(
                """UPDATE restock_orders 
                   SET status = 'completed', razorpay_order_id = ?
                   WHERE id = ?""",
                (razorpay_order_id, order_id)
            )
        else:
            cursor.execute(
                "UPDATE restock_orders SET status = 'completed' WHERE id = ?",
                (order_id,)
            )
        
        conn.commit()
        conn.close()
        return True
        
    except Exception as e:
        conn.rollback()
        conn.close()
        return False


def reject_restock_order(order_id: int) -> bool:
    """Reject a pending restock order."""
    conn = _get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "UPDATE restock_orders SET status = 'rejected' WHERE id = ? AND status = 'pending'",
            (order_id,)
        )
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    except Exception as e:
        conn.rollback()
        conn.close()
        return False


# Initialize database on module import
init_database()