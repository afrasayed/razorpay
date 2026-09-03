"""
The upsell agent never invents a product. It only ever suggests something
that (a) exists in the catalog, (b) is declared as an upsell_id of something
already in the cart, and (c) would keep the cart under the session cap. It
offers **at most one** upsell per checkout so the buyer-agent isn't spammed,
and every suggestion carries a machine-checkable reason.
"""
from . import catalog, config


def suggest(cart: list, session_spend_so_far_paise: int):
    """cart: list of {product, qty, line_total_paise}
    Returns (upsell_product_or_None, reason_str)."""
    cart_total = sum(item["line_total_paise"] for item in cart)
    in_cart_ids = {item["product"]["id"] for item in cart}

    for item in cart:
        for candidate in catalog.upsell_candidates(item["product"]["id"]):
            if candidate["id"] in in_cart_ids:
                continue
            projected = session_spend_so_far_paise + cart_total + candidate["price_paise"]
            if projected > config.MAX_SESSION_SPEND_PAISE:
                continue  # would breach the session cap -- skip, don't suggest and fail later
            reason = (
                f"'{candidate['name']}' is paired with '{item['product']['name']}' in the "
                f"catalog's upsell mapping, and adding it keeps the session at "
                f"INR {projected/100:.2f} of the INR {config.MAX_SESSION_SPEND_PAISE/100:.2f} cap."
            )
            return candidate, reason
    return None, "No catalog-declared upsell fits under the remaining session budget."
