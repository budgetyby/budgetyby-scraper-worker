"""
BudgetBy — Deal Detector
Implements the cascading comparison logic for detecting deals.
"""

import logging
from asyncpg import Record
from budgetby import config

logger = logging.getLogger("budgetby.engine.deal_detector")

async def detect_deal(product: Record, new_price: float) -> dict | None:
    """
    Detects if a new_price constitutes a REAL deal based on a 5-level cascading comparison.
    Returns a dict with deal details or None if it's not a valid qualifying deal.
    """
    try:
        if not new_price or new_price <= 0:
            return None

        new_price = float(new_price)
        current_price = float(product.get("current_price")) if product.get("current_price") is not None else None
        previous_price = float(product.get("previous_price")) if product.get("previous_price") is not None else None
        mrp = float(product.get("mrp")) if product.get("mrp") is not None else new_price
        
        # 1. Strict MRP Savings Filter: Deal MUST have at least 10% discount from MRP and at least ₹30 savings
        if mrp <= new_price or mrp <= 0:
            return None
            
        mrp_savings = mrp - new_price
        mrp_discount_pct = mrp_savings / mrp
        if mrp_discount_pct < 0.10 or mrp_savings < 30:
            return None

        # 2. Strict Price Drop Filter: Price MUST have actually dropped from previous or current price
        # Drop must be >= 3% AND >= ₹20
        has_dropped = False
        baseline = previous_price if (previous_price and previous_price > new_price) else current_price
        if baseline and baseline > new_price:
            drop_val = baseline - new_price
            drop_pct = drop_val / baseline
            if drop_pct >= 0.03 or drop_val >= 20:
                has_dropped = True
                
        # If product is newly discovered and has 25%+ discount, allow it
        if (not baseline or baseline <= new_price) and mrp_discount_pct >= 0.25:
            has_dropped = True

        if not has_dropped:
            return None

        # 3. Variant Mismatch Anomaly Guard:
        # Extreme drops (>75% drop on items > ₹2,000) are almost always SKU variant switches (e.g. 10ml mini vs 60ml jar)
        if baseline and baseline > 2000 and new_price < (baseline * 0.25):
            logger.warning(f"Rejecting deal candidate due to extreme variant switch divergence: {product.get('title')} (₹{baseline} -> ₹{new_price})")
            return None

        margins = config.BENCHMARK_MARGINS
        
        min_30d = float(product.get("min_30d")) if product.get("min_30d") is not None else None
        min_60d = float(product.get("min_60d")) if product.get("min_60d") is not None else None
        min_90d = float(product.get("min_90d")) if product.get("min_90d") is not None else None
        all_time_low = float(product.get("all_time_low")) if product.get("all_time_low") is not None else None
        median_30d = float(product.get("median_30d_price")) if product.get("median_30d_price") is not None else None
        
        category = product.get("category", "default")
        cat_min_drop_pct, cat_min_savings = config.CATEGORY_MIN_DROPS.get(
            category, config.DEFAULT_MIN_DROP
        )

        best_badge = None
        all_badges = []
        should_post = False
        deal_type = "price_drop"

        # Level 4: All Time Low
        if all_time_low and all_time_low > 0:
            if new_price <= all_time_low:
                all_badges.append("ATL")
                best_badge = "ATL"
                should_post = True
            elif new_price <= all_time_low * (1 + margins["all_time_low"]):
                all_badges.append("near_ATL")
                if not best_badge:
                    best_badge = "near_ATL"
                should_post = True

        # Level 3: 90-day Low
        if min_90d and min_90d > 0 and new_price <= min_90d * (1 + margins["min_90d"]):
            all_badges.append("90d_low")
            if not best_badge:
                best_badge = "90d_low"
            should_post = True

        # Level 2: 60-day Low
        if min_60d and min_60d > 0 and new_price <= min_60d * (1 + margins["min_60d"]):
            all_badges.append("60d_low")
            if not best_badge:
                best_badge = "60d_low"
            should_post = True

        # Level 1: 30-day Low
        if min_30d and min_30d > 0 and new_price <= min_30d * (1 + margins["min_30d"]):
            all_badges.append("30d_low")
            if not best_badge:
                best_badge = "30d_low"
            should_post = True

        # Level 5: Median Drop
        if median_30d and median_30d > 0:
            drop_amount = median_30d - new_price
            drop_pct = drop_amount / median_30d
            if drop_pct >= cat_min_drop_pct and drop_amount >= cat_min_savings:
                all_badges.append("median_drop")
                if not best_badge:
                    best_badge = "median_drop"
                should_post = True

        # Default fallback badge if significant discount
        if not should_post and mrp_discount_pct >= 0.25:
            best_badge = "PRICE_DROP"
            all_badges.append("PRICE_DROP")
            should_post = True

        if not should_post:
            return None

        # Check fake discount
        from budgetby.engine import fake_discount
        is_fake = await fake_discount.is_fake_discount(product, new_price)
        if is_fake:
            logger.info(f"Skipping fake discount product #{product.get('id')} ({product.get('title')[:30]})")
            return None

        return {
            "should_post": True,
            "badge": best_badge or "PRICE_DROP",
            "all_badges": all_badges,
            "savings_amount": mrp_savings,
            "savings_pct": mrp_discount_pct,
            "deal_type": deal_type,
            "is_fake_discount": False,
        }
    except Exception as e:
        logger.error(f"Error in detect_deal: {e}")
        return None
