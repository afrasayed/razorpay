"""
Loads catalog.json and exposes it for multi-company agent checkout:

1. get_manifest(company) -- the raw, agent-readable manifest for a company.
2. find_product(id) / search(text, company) -- lookups this merchant's own checkout
   agent uses to resolve a buyer's request into real SKUs.
3. get_companies() -- list of all available companies.
"""
import json
import os
from typing import Optional, List, Dict, Any

_CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")

with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
    _RAW = json.load(f)

_PRODUCTS_BY_ID = {p["id"]: p for p in _RAW["products"]}
_COMPANIES = list(_RAW.get("companies", {
    "Afra Infra": _RAW.get("merchant", {}),
    "Tropicana": {},
    "Amul": {},
    "Minimalist": {},
    "Nestle": {}
}).keys())


def get_companies() -> List[str]:
    """Return list of supported companies."""
    return list(_COMPANIES)


def get_manifest(company: Optional[str] = None) -> dict:
    """Return catalog manifest, optionally filtered by company."""
    if not company:
        return _RAW
    
    # Normalize company lookup
    matched_company = None
    for c in _COMPANIES:
        if company.lower() == c.lower() or company.lower() in c.lower() or c.lower() in company.lower():
            matched_company = c
            break
            
    if not matched_company:
        matched_company = "Afra Infra"
        
    merchant_info = _RAW.get("companies", {}).get(matched_company, _RAW.get("merchant"))
    products = [p for p in _RAW["products"] if p.get("company") == matched_company]
    
    return {
        "company": matched_company,
        "merchant": merchant_info,
        "products": products
    }


def find_product(product_id: str):
    return _PRODUCTS_BY_ID.get(product_id)


def search(text: str, company: Optional[str] = None):
    """Very small keyword matcher standing in for the buyer-agent's own retrieval."""
    text = text.lower()
    pool = _RAW["products"]
    if company:
        pool = [p for p in pool if p.get("company", "").lower() == company.lower()]
    return [p for p in pool if text in p["name"].lower() or text in p["description"].lower()]


def upsell_candidates(product_id: str):
    p = find_product(product_id)
    if not p:
        return []
    return [find_product(uid) for uid in p.get("upsell_ids", []) if find_product(uid)]
