"""
Loads catalog.json and exposes it two ways:

1. get_manifest() -- the raw, agent-readable manifest. This is what an
   external AI buyer agent would fetch from a `.well-known/agent-catalog.json`
   style endpoint before it ever talks to a human-facing checkout page.
2. find_product(id) / search(text) -- lookups this merchant's own checkout
   agent uses to resolve a buyer's request into real SKUs.
"""
import json
import os

_CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")

with open(_CATALOG_PATH) as f:
    _RAW = json.load(f)

_PRODUCTS_BY_ID = {p["id"]: p for p in _RAW["products"]}


def get_manifest() -> dict:
    return _RAW


def find_product(product_id: str):
    return _PRODUCTS_BY_ID.get(product_id)


def search(text: str):
    """Very small keyword matcher standing in for the buyer-agent's own
    retrieval -- this merchant only needs to expose the catalog, not do the
    buyer's NLU for it."""
    text = text.lower()
    return [p for p in _RAW["products"] if text in p["name"].lower() or text in p["description"].lower()]


def upsell_candidates(product_id: str):
    p = find_product(product_id)
    if not p:
        return []
    return [find_product(uid) for uid in p.get("upsell_ids", []) if find_product(uid)]
