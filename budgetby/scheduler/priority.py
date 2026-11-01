"""
BudgetBy — Adaptive Priority Assignment
Assigns dynamic priority tiers 1–4 based on product volatility, deal history, discovery recency, and stock state:
- Tier 1 (1 hour): Active price drop within 24h, recent deal within 48h, lightning deal / flash deal.
- Tier 2 (3 hours): Newly discovered product (<48h), stock recovery (TEMP_OOS -> ACTIVE), high-volatility category, bestseller.
- Tier 3 (18 hours): Standard active catalog product.
- Tier 4 (48 hours): Out of stock (TEMP_OOS), dormant product (unchanged >14 days with low review count).
"""

import datetime
from typing import Union, Dict, Any
from budgetby import config


def assign_priority(product: Union[Dict[str, Any], Any]) -> int:
    """
    Assign priority tier 1-4 based on local product volatility and lifecycle metrics.
    Returns integer 1-4.
    """
    if not product or not hasattr(product, "get"):
        return 3

    now = datetime.datetime.now(datetime.timezone.utc)
    status = str(product.get("status") or "").upper().strip()

    # 1. Out of Stock (TEMP_OOS) -> Demote immediately to Tier 4 (conserves scraper bandwidth)
    if status == config.STATUS_TEMP_OOS or status == "TEMP_OOS":
        return 4

    # 2. Tier 1 (Critical - 1h): Recent price change in last 24h, active deal in last 48h, lightning deal
    if product.get("has_recent_deal") or product.get("is_lightning_deal") or product.get("is_movers_shakers"):
        return 1

    last_change = product.get("last_price_change")
    if last_change:
        if isinstance(last_change, str):
            try:
                last_change = datetime.datetime.fromisoformat(last_change.replace("Z", "+00:00"))
            except Exception:
                last_change = None
        if last_change and last_change.tzinfo is None:
            last_change = last_change.replace(tzinfo=datetime.timezone.utc)
        if last_change and (now - last_change).total_seconds() < 86400:
            return 1

    # 3. Tier 2 (High - 3h): Newly discovered product (<48h) or high-volatility category / recovery
    # Check if newly discovered product
    created_at = product.get("created_at") or product.get("first_seen")
    if created_at:
        if isinstance(created_at, str):
            try:
                created_at = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except Exception:
                created_at = None
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=datetime.timezone.utc)
        if created_at and (now - created_at).total_seconds() < 2 * 86400:
            return 2

    # Stock recovery boost
    if product.get("recovered_from_oos"):
        return 2

    category = str(product.get("category") or "").lower()
    review_count = int(product.get("review_count") or 0)
    
    # High-velocity promotional categories or bestsellers
    if category in ("fashion", "beauty", "electronics", "appliances", "smartphones") or review_count >= 5000 or product.get("is_bestseller"):
        return 2

    # 4. Tier 4 (Dormant - 48h): Unchanged for 14+ days with low reviews
    if last_change and (now - last_change).total_seconds() > 14 * 86400 and review_count < 1000:
        return 4

    # 5. Tier 3 (Standard - 18h): Standard catalog
    return 3
