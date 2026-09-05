"""
End-to-End Multi-Company Verification Test
Verifies:
1. Roofing business (Afra Infra) has all original products and stock intact.
2. All 5 companies (Afra Infra, Tropicana, Amul, Minimalist, Nestle) are seeded and distinct.
3. Switching between at least 3 companies (Afra Infra, Tropicana, Amul) works.
4. Simulated customer orders in each company deplete only that company's stock.
5. Auto-restock threshold check only considers the active company's low stock.
6. Audit entries are tagged with company and filtered per-company with zero data bleed.
"""
import requests
import json

BASE_URL = "http://127.0.0.1:8000"

def test_multi_company():
    print("==================================================")
    print("RUNNING MULTI-COMPANY END-TO-END VERIFICATION")
    print("==================================================")
    
    # 1. Reset inventory to known starting state
    print("\n--- 1. Resetting Inventory ---")
    r = requests.post(f"{BASE_URL}/inventory/reset")
    assert r.status_code == 200, f"Reset failed: {r.text}"
    print("[OK] Inventory reset cleanly.")

    # 2. Verify all 5 companies returned by /companies
    print("\n--- 2. Checking /companies Endpoint ---")
    r = requests.get(f"{BASE_URL}/companies")
    assert r.status_code == 200, f"Failed to get companies: {r.text}"
    companies = r.json()
    print(f"Supported companies ({len(companies)}): {companies}")
    expected_names = {"Afra Infra", "Tropicana", "Amul", "Minimalist", "Nestle"}
    actual_names = set(companies)
    assert expected_names == actual_names, f"Expected {expected_names}, got {actual_names}"
    print("[OK] All 5 companies present.")

    # 3. Verify Roofing (Afra Infra) original products & stock intact
    print("\n--- 3. Verifying Roofing (Afra Infra) Original Inventory ---")
    r = requests.get(f"{BASE_URL}/inventory?company=Afra Infra")
    assert r.status_code == 200
    roofing_items = r.json()
    print(f"Roofing inventory count: {len(roofing_items)}")
    roofing_skus = {item["sku"]: item["quantity"] for item in roofing_items}
    
    # Original roofing SKUs & seed levels
    expected_roofing = {
        "sku_roof_sheet_std": 50,
        "sku_roof_sheet_premium": 30,
        "sku_clay_tile": 100,
        "sku_ridge_cap": 40,
        "sku_tile_sealant": 50,
        "sku_industrial_bulk_order": 2,
        "sku_installation_basic": 9999,
        "sku_installation_premium": 9999,
    }
    for sku, expected_qty in expected_roofing.items():
        assert sku in roofing_skus, f"Missing original roofing SKU: {sku}"
        assert roofing_skus[sku] == expected_qty, f"Roofing SKU {sku} quantity altered! Expected {expected_qty}, got {roofing_skus[sku]}"
    print("[OK] Roofing inventory 100% intact with all 8 original SKUs and quantities!")

    # 4. Verify the other 4 companies have their 5 products each
    print("\n--- 4. Verifying Seeding for 4 New Consumer Brands ---")
    for comp in ["Tropicana", "Amul", "Minimalist", "Nestle"]:
        r = requests.get(f"{BASE_URL}/inventory?company={comp}")
        assert r.status_code == 200
        items = r.json()
        print(f"  {comp}: {len(items)} products -> {[i['name'] for i in items]}")
        assert len(items) == 5, f"Expected 5 products for {comp}, found {len(items)}"
        # Verify every item is strictly tagged with this company
        for i in items:
            assert i["company"] == comp, f"Item {i['sku']} has company {i['company']}, expected {comp}"
    print("[OK] All 4 new companies correctly seeded with 5 products each (20 new + 8 roofing = 28 total).")

    # 5. Test Customer Order Simulation & Stock Depletion in Company A: Tropicana
    print("\n--- 5. Simulating Order in Tropicana ---")
    r_gen = requests.post(f"{BASE_URL}/customer-order/generate", json={"company": "Tropicana"})
    assert r_gen.status_code == 200
    order_data = r_gen.json()
    print(f"  Generated order: {order_data}")
    assert order_data["sku"].startswith("sku_trop_"), f"Tropicana generated non-Tropicana SKU: {order_data['sku']}"
    
    # Place customer order for Tropicana SKU
    target_sku = "sku_trop_orange_1l"
    r_before = requests.get(f"{BASE_URL}/inventory?company=Tropicana")
    stock_before = next(i["quantity"] for i in r_before.json() if i["sku"] == target_sku)
    
    order_qty = 5
    r_order = requests.post(f"{BASE_URL}/customer-order", json={
        "items": [{"sku": target_sku, "qty": order_qty}],
        "notes": "Convenience store morning order for fresh orange juice",
        "company": "Tropicana",
        "auto_restock": False
    })
    assert r_order.status_code == 200, f"Order failed: {r_order.text}"
    r_after = requests.get(f"{BASE_URL}/inventory?company=Tropicana")
    stock_after = next(i["quantity"] for i in r_after.json() if i["sku"] == target_sku)
    assert stock_after == stock_before - order_qty, f"Stock didn't deplete correctly: before={stock_before}, after={stock_after}"
    print(f"[OK] Tropicana stock depleted: {stock_before} -> {stock_after} (Ordered: {order_qty})")

    # Verify no bleed into Amul or Afra Infra
    r_roofing_check = requests.get(f"{BASE_URL}/inventory?company=Afra Infra")
    assert next(i["quantity"] for i in r_roofing_check.json() if i["sku"] == "sku_roof_sheet_std") == 50
    print("[OK] Zero bleed into Afra Infra inventory.")

    # 6. Test Customer Order Simulation & Auto-Restock in Company B: Amul
    print("\n--- 6. Simulating Order & Auto-Restock in Amul ---")
    target_amul_sku = "sku_amul_butter_500g"
    r_amul_before = requests.get(f"{BASE_URL}/inventory?company=Amul")
    amul_stock = next(i["quantity"] for i in r_amul_before.json() if i["sku"] == target_amul_sku)
    threshold = next(i["reorder_threshold"] for i in r_amul_before.json() if i["sku"] == target_amul_sku)
    print(f"  Amul Butter stock before: {amul_stock} (threshold: {threshold})")
    
    # Deplete below threshold (initial: 90, threshold: 25 -> order 70)
    r_amul_order = requests.post(f"{BASE_URL}/customer-order", json={
        "items": [{"sku": target_amul_sku, "qty": 70}],
        "notes": "Bakery weekly replenishment for butter",
        "company": "Amul",
        "auto_restock": True
    })
    assert r_amul_order.status_code == 200
    amul_res = r_amul_order.json()
    assert amul_res["company"] == "Amul", f"Response company mismatch: {amul_res.get('company')}"
    assert "auto_restock" in amul_res, "Auto restock should have triggered!"
    assert amul_res["auto_restock"]["company"] == "Amul"
    print(f"  Auto restock triggered for Amul: {amul_res['auto_restock']['items_below_threshold']} items below threshold")
    
    # Check Amul inventory check vs Tropicana check
    amul_low = requests.get(f"{BASE_URL}/inventory/check?company=Amul").json()
    trop_low = requests.get(f"{BASE_URL}/inventory/check?company=Tropicana").json()
    print(f"  Amul low stock items: {[i['sku'] for i in amul_low]}")
    print(f"  Tropicana low stock items: {[i['sku'] for i in trop_low]}")
    assert any(i["sku"] == target_amul_sku for i in amul_low), "Amul low stock missing butter"
    assert not any(i["sku"] == target_amul_sku for i in trop_low), "LEAKAGE! Amul SKU leaked into Tropicana low stock check!"
    print("[OK] Restock check operates strictly per-company with NO cross-company leakage.")

    # 7. Test Customer Order Simulation in Company C: Minimalist
    print("\n--- 7. Simulating Order in Minimalist ---")
    r_gen_min = requests.post(f"{BASE_URL}/customer-order/generate", json={"company": "Minimalist"})
    assert r_gen_min.status_code == 200
    min_order = r_gen_min.json()
    print(f"  Generated Minimalist order: {min_order}")
    assert min_order["sku"].startswith("sku_mini_"), f"Minimalist generated non-Minimalist SKU: {min_order['sku']}"
    
    # Deplete 5 units of Salicylic Cleanser
    min_sku = "sku_mini_salicylic_cleanser"
    r_min_before = requests.get(f"{BASE_URL}/inventory?company=Minimalist")
    min_stock_before = next(i["quantity"] for i in r_min_before.json() if i["sku"] == min_sku)
    r_min_order = requests.post(f"{BASE_URL}/customer-order", json={
        "items": [{"sku": min_sku, "qty": 5}],
        "notes": min_order.get("reason", "Pharmacy beauty aisle refill"),
        "company": "Minimalist",
        "auto_restock": False
    })
    assert r_min_order.status_code == 200
    r_min_after = requests.get(f"{BASE_URL}/inventory?company=Minimalist")
    min_stock_after = next(i["quantity"] for i in r_min_after.json() if i["sku"] == min_sku)
    assert min_stock_after == min_stock_before - 5
    print(f"[OK] Minimalist stock depleted: {min_stock_before} -> {min_stock_after}")

    # 8. Verify Audit Trail Isolation
    print("\n--- 8. Verifying Audit Trail Isolation & History ---")
    r_roof_audit = requests.get(f"{BASE_URL}/audit?company=Afra Infra")
    roof_entries = r_roof_audit.json()
    print(f"  Afra Infra audit entries: {len(roof_entries)}")
    assert len(roof_entries) > 0, "Roofing business should retain its historical audit trail entries!"
    for e in roof_entries[:5]:
        assert e.get("company", "Afra Infra") == "Afra Infra"

    r_amul_audit = requests.get(f"{BASE_URL}/audit?company=Amul")
    amul_entries = r_amul_audit.json()
    print(f"  Amul audit entries: {len(amul_entries)}")
    assert len(amul_entries) > 0
    for e in amul_entries:
        assert e["company"] == "Amul", f"Audit leak! Non-Amul entry in Amul audit: {e}"

    r_trop_audit = requests.get(f"{BASE_URL}/audit?company=Tropicana")
    trop_entries = r_trop_audit.json()
    print(f"  Tropicana audit entries: {len(trop_entries)}")
    for e in trop_entries:
        assert e["company"] == "Tropicana", f"Audit leak! Non-Tropicana entry in Tropicana audit: {e}"

    # Confirm Roofing audit history does NOT contain Tropicana or Amul events
    assert not any(e.get("company") == "Tropicana" for e in roof_entries), "Leak: Tropicana entry in Afra Infra audit!"
    assert not any(e.get("company") == "Amul" for e in roof_entries), "Leak: Amul entry in Afra Infra audit!"
    print("[OK] Audit trail entries are strictly isolated with zero cross-company bleed.")

    print("\n==================================================")
    print("ALL MULTI-COMPANY VERIFICATION TESTS PASSED [OK]")
    print("==================================================")

if __name__ == "__main__":
    test_multi_company()
