"""
Inventory management using SQLite database with multi-company support.

This module handles:
- Stock level tracking for SKUs across multiple companies
- Customer order processing (depleting stock per company)
- Restock operations (increasing stock per company)
- Reorder threshold checking per company
"""
import sqlite3
import os
import json
from typing import List, Dict, Optional
from datetime import datetime

# Database path
_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "inventory.db")

# Ensure data directory exists
os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)

DEFAULT_COMPANY = "Afra Infra"

ALL_SEED_DATA = [
    # 1. Afra Infra (Existing Roofing business - intact)
    ("sku_roof_sheet_std", "Standard Galvanised Roofing Sheet", 50, 15, "Afra Infra"),
    ("sku_roof_sheet_premium", "Premium Coated Roofing Sheet", 30, 10, "Afra Infra"),
    ("sku_clay_tile", "Terracotta Clay Roof Tile", 100, 20, "Afra Infra"),
    ("sku_ridge_cap", "Ridge Cap Set", 40, 10, "Afra Infra"),
    ("sku_tile_sealant", "Tile Sealant (5L)", 50, 15, "Afra Infra"),
    ("sku_installation_basic", "Basic Installation Labour", 9999, 1000, "Afra Infra"),
    ("sku_installation_premium", "Premium Installation + Site Inspection", 9999, 1000, "Afra Infra"),
    ("sku_industrial_bulk_order", "Industrial Bulk Sheet Pallet", 2, 1, "Afra Infra"),
    
    # 2. Tropicana (Juices & Beverages)
    ("sku_trop_orange_1l", "Tropicana 100% Orange Juice (1L)", 80, 20, "Tropicana"),
    ("sku_trop_apple_1l", "Tropicana Apple Delight Juice (1L)", 65, 15, "Tropicana"),
    ("sku_trop_mixed_1l", "Tropicana Mixed Fruit Blend (1L)", 70, 18, "Tropicana"),
    ("sku_trop_guava_1l", "Tropicana Premium Guava Nectar (1L)", 50, 12, "Tropicana"),
    ("sku_trop_sparkling_grape", "Tropicana Sparkling White Grape (750ml)", 40, 10, "Tropicana"),
    
    # 3. Amul (Dairy & Fresh Products)
    ("sku_amul_butter_500g", "Amul Pasteurised Butter (500g)", 90, 25, "Amul"),
    ("sku_amul_gold_1l", "Amul Gold Full Cream Milk (1L Tetra)", 120, 30, "Amul"),
    ("sku_amul_cheese_400g", "Amul Processed Cheese Block (400g)", 60, 15, "Amul"),
    ("sku_amul_ghee_1l", "Amul Pure Cow Ghee Tin (1L)", 45, 10, "Amul"),
    ("sku_amul_dark_choc", "Amul 55% Rich Dark Chocolate Bar (150g)", 85, 20, "Amul"),
    
    # 4. Minimalist (Active Skincare)
    ("sku_mini_niacinamide", "Minimalist Niacinamide 10% Face Serum (30ml)", 55, 15, "Minimalist"),
    ("sku_mini_salicylic_cleanser", "Minimalist Salicylic Acid 2% Face Cleanser (100ml)", 75, 20, "Minimalist"),
    ("sku_mini_vit_c", "Minimalist Vitamin C 10% Glow Serum (30ml)", 40, 10, "Minimalist"),
    ("sku_mini_sunscreen_spf50", "Minimalist Multi-Vitamin SPF 50 Sunscreen (50g)", 90, 25, "Minimalist"),
    ("sku_mini_marula_moisturizer", "Minimalist Marula Oil 5% Intense Moisturizer (50g)", 60, 15, "Minimalist"),
    
    # 5. Nestle (Packaged Foods & Everyday Pantry)
    ("sku_nestle_maggi_12p", "Maggi 2-Minute Masala Noodles (Pack of 12)", 110, 30, "Nestle"),
    ("sku_nestle_nescafe_200g", "Nescafe Classic Instant Coffee Jar (200g)", 50, 12, "Nestle"),
    ("sku_nestle_everyday_1kg", "Nestle Everyday Dairy Whitener (1kg Pouch)", 45, 10, "Nestle"),
    ("sku_nestle_kitkat_share", "KitKat 4-Finger Chocolate Share Bag (Pack of 8)", 80, 20, "Nestle"),
    ("sku_nestle_milkmaid_400g", "Nestle Milkmaid Sweetened Condensed Milk (400g)", 65, 15, "Nestle")
]


def _get_connection():
    """Get SQLite connection with row factory for dict access."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(cursor, table: str, column: str, col_type: str = "TEXT", default_val: str = "Afra Infra"):
    """Safely add a column to a table if it does not already exist."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row["name"] for row in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type} DEFAULT '{default_val}'")


def init_database():
    """Initialize inventory database with schema and seed data, handling migrations."""
    conn = _get_connection()
    cursor = conn.cursor()
    
    # Create inventory table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            sku TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            reorder_threshold INTEGER NOT NULL DEFAULT 10,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            company TEXT DEFAULT 'Afra Infra'
        )
    """)
    
    # Create customer orders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customer_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            items TEXT NOT NULL,  -- JSON array of {sku, qty}
            total_paise INTEGER NOT NULL,
            notes TEXT,
            company TEXT DEFAULT 'Afra Infra'
        )
    """)
    
    # Create restock orders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS restock_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            items TEXT NOT NULL,  -- JSON array of {sku, qty}
            total_paise INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            auditor_verdict TEXT,
            auditor_reasoning TEXT,
            ai_decision_context TEXT,
            human_approval_date TIMESTAMP,
            razorpay_order_id TEXT,
            company TEXT DEFAULT 'Afra Infra'
        )
    """)
    
    # Run column migrations for existing databases
    _ensure_column(cursor, "inventory", "company", "TEXT", "Afra Infra")
    _ensure_column(cursor, "customer_orders", "company", "TEXT", "Afra Infra")
    _ensure_column(cursor, "restock_orders", "company", "TEXT", "Afra Infra")
    
    # Ensure existing rows without company are tagged with Afra Infra
    cursor.execute("UPDATE inventory SET company = 'Afra Infra' WHERE company IS NULL OR company = ''")
    cursor.execute("UPDATE customer_orders SET company = 'Afra Infra' WHERE company IS NULL OR company = ''")
    cursor.execute("UPDATE restock_orders SET company = 'Afra Infra' WHERE company IS NULL OR company = ''")
    conn.commit()
    
    # Seed any missing products from all 5 companies (INSERT OR IGNORE keeps existing stock levels 100% intact!)
    for sku, name, qty, threshold, comp in ALL_SEED_DATA:
        cursor.execute(
            """INSERT OR IGNORE INTO inventory (sku, name, quantity, reorder_threshold, company)
               VALUES (?, ?, ?, ?, ?)""",
            (sku, name, qty, threshold, comp)
        )
        # In case row existed prior without company, update company field
        cursor.execute(
            "UPDATE inventory SET company = ? WHERE sku = ? AND (company IS NULL OR company = '')",
            (comp, sku)
        )
        
    conn.commit()
    conn.close()


def _normalize_company(company: Optional[str]) -> Optional[str]:
    """Resolve user company input to canonical company name."""
    if not company:
        return None
    comp_lower = company.strip().lower()
    if "roof" in comp_lower or "afra" in comp_lower or "infra" in comp_lower:
        return "Afra Infra"
    if "tropicana" in comp_lower or "juice" in comp_lower:
        return "Tropicana"
    if "amul" in comp_lower or "dairy" in comp_lower:
        return "Amul"
    if "minimalist" in comp_lower or "skincare" in comp_lower:
        return "Minimalist"
    if "nestle" in comp_lower or "maggi" in comp_lower:
        return "Nestle"
    return company.strip()


def reset_inventory(company: Optional[str] = None):
    """Reset inventory table to clean initial seed levels, optionally for a specific company."""
    conn = _get_connection()
    cursor = conn.cursor()
    target_comp = _normalize_company(company)
    
    if target_comp:
        cursor.execute("DELETE FROM inventory WHERE company = ?", (target_comp,))
        comp_seeds = [s for s in ALL_SEED_DATA if s[4] == target_comp]
        for sku, name, qty, threshold, comp in comp_seeds:
            cursor.execute(
                "INSERT INTO inventory (sku, name, quantity, reorder_threshold, company) VALUES (?, ?, ?, ?, ?)",
                (sku, name, qty, threshold, comp)
            )
    else:
        cursor.execute("DELETE FROM inventory")
        for sku, name, qty, threshold, comp in ALL_SEED_DATA:
            cursor.execute(
                "INSERT INTO inventory (sku, name, quantity, reorder_threshold, company) VALUES (?, ?, ?, ?, ?)",
                (sku, name, qty, threshold, comp)
            )
            
    conn.commit()
    conn.close()


def get_inventory(company: Optional[str] = None) -> List[Dict]:
    """Get current inventory levels, optionally filtered by company."""
    conn = _get_connection()
    cursor = conn.cursor()
    target_comp = _normalize_company(company)
    
    if target_comp:
        cursor.execute("SELECT * FROM inventory WHERE company = ? ORDER BY sku", (target_comp,))
    else:
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


def process_customer_order(items: List[Dict], notes: str = "", company: Optional[str] = None) -> Dict:
    """
    Process a customer order - deplete inventory for the company.
    
    Args:
        items: List of {"sku": str, "qty": int}
        notes: Optional notes about the order
        company: Optional company name
    
    Returns:
        Dict with success status, message, and order details
    """
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    import catalog as catalog_module
    
    conn = _get_connection()
    cursor = conn.cursor()
    
    total_paise = 0
    processed_items = []
    insufficient_stock = []
    
    try:
        resolved_company = _normalize_company(company)
        
        # Calculate total and check stock
        for item in items:
            sku = item["sku"]
            qty = item["qty"]
            
            # Get product info from catalog
            product = catalog_module.find_product(sku)
            if not product:
                insufficient_stock.append(f"{sku} - unknown product")
                continue
                
            if not resolved_company:
                resolved_company = product.get("company", DEFAULT_COMPANY)
            
            # Check current stock
            cursor.execute("SELECT quantity, reorder_threshold, company FROM inventory WHERE sku = ?", (sku,))
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
                "threshold": reorder_threshold,
                "company": row["company"] or resolved_company or DEFAULT_COMPANY
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
        
        order_comp = resolved_company or processed_items[0].get("company", DEFAULT_COMPANY)
        
        # Log customer order with company
        cursor.execute(
            """INSERT INTO customer_orders (items, total_paise, notes, company) 
               VALUES (?, ?, ?, ?)""",
            (json.dumps(processed_items), total_paise, notes, order_comp)
        )
        order_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "message": f"Order processed successfully. Total: INR {total_paise/100:.2f}",
            "items": processed_items,
            "total_paise": total_paise,
            "order_id": order_id,
            "company": order_comp
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


def get_items_below_threshold(company: Optional[str] = None) -> List[Dict]:
    """Get items that are below their reorder threshold, optionally filtered by company."""
    conn = _get_connection()
    cursor = conn.cursor()
    target_comp = _normalize_company(company)
    
    if target_comp:
        cursor.execute("""
            SELECT * FROM inventory 
            WHERE quantity <= reorder_threshold AND company = ?
            ORDER BY quantity ASC
        """, (target_comp,))
    else:
        cursor.execute("""
            SELECT * FROM inventory 
            WHERE quantity <= reorder_threshold 
            ORDER BY quantity ASC
        """)
        
    items = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return items


def update_stock(sku: str, quantity_change: int) -> bool:
    """Update stock level for a SKU (positive to add, negative to remove)."""
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


def get_customer_orders(limit: int = 20, company: Optional[str] = None) -> List[Dict]:
    """Get recent customer orders, optionally filtered by company."""
    conn = _get_connection()
    cursor = conn.cursor()
    target_comp = _normalize_company(company)
    
    if target_comp:
        # If querying for Afra Infra, also include legacy NULL company entries
        if target_comp == "Afra Infra":
            cursor.execute("""
                SELECT * FROM customer_orders 
                WHERE company = ? OR company IS NULL OR company = ''
                ORDER BY order_date DESC 
                LIMIT ?
            """, (target_comp, limit))
        else:
            cursor.execute("""
                SELECT * FROM customer_orders 
                WHERE company = ?
                ORDER BY order_date DESC 
                LIMIT ?
            """, (target_comp, limit))
    else:
        cursor.execute("""
            SELECT * FROM customer_orders 
            ORDER BY order_date DESC 
            LIMIT ?
        """, (limit,))
        
    orders = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return orders


def get_restock_orders(limit: int = 20, company: Optional[str] = None) -> List[Dict]:
    """Get recent restock orders, optionally filtered by company."""
    conn = _get_connection()
    cursor = conn.cursor()
    target_comp = _normalize_company(company)
    
    if target_comp:
        if target_comp == "Afra Infra":
            cursor.execute("""
                SELECT * FROM restock_orders 
                WHERE company = ? OR company IS NULL OR company = ''
                ORDER BY order_date DESC 
                LIMIT ?
            """, (target_comp, limit))
        else:
            cursor.execute("""
                SELECT * FROM restock_orders 
                WHERE company = ?
                ORDER BY order_date DESC 
                LIMIT ?
            """, (target_comp, limit))
    else:
        cursor.execute("""
            SELECT * FROM restock_orders 
            ORDER BY order_date DESC 
            LIMIT ?
        """, (limit,))
        
    orders = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return orders


def create_restock_order(items: List[Dict], total_paise: int, ai_decision_context: str = "", company: str = "Afra Infra") -> int:
    """Create a new pending restock order record."""
    conn = _get_connection()
    cursor = conn.cursor()
    
    order_comp = _normalize_company(company) or DEFAULT_COMPANY
    
    cursor.execute(
        """INSERT INTO restock_orders 
           (items, total_paise, ai_decision_context, status, company) 
           VALUES (?, ?, ?, 'pending', ?)""",
        (json.dumps(items), total_paise, ai_decision_context, order_comp)
    )
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id


def update_restock_order_status(order_id: int, status: str, 
                                auditor_verdict: str = None,
                                auditor_reasoning: str = None) -> bool:
    """Update restock order status and auditor verdict."""
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
    """Mark a restock order as completed and update inventory."""
    conn = _get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT items, status FROM restock_orders WHERE id = ?", (order_id,))
        row = cursor.fetchone()
        
        if not row or row["status"] not in ["approved", "pending"]:
            conn.close()
            return False
        
        items = json.loads(row["items"])
        for item in items:
            sku = item.get("sku") or item.get("product_id")
            qty = item.get("qty", 1)
            if sku:
                update_stock(sku, qty)
        
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


# Initialize database and migrate on module load
init_database()