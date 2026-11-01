"""
BudgetBy — Multi-Day MRP History & Fake-Discount Detection
Protects users from fake discounts caused by sudden MRP spikes or gradual MRP creeping.
Zero Supabase egress: reads local SQLite mrp_history table.
"""

import logging
import statistics
from typing import Union, Dict, Any

logger = logging.getLogger("budgetby.engine.fake_discount")

async def is_fake_discount(product: Union[Dict[str, Any], Any], new_price: float) -> bool:
    """
    Checks if the product has a fake discount using 14-day local MRP history.
    
    Detection Rules:
    1. Sudden MRP Spike: Current MRP is >20% above 14-day historical median MRP while deal price is not a true discount.
    2. Gradual MRP Creeping: Current MRP crept >25% above 14-day minimum MRP while deal price is near the old regular baseline.
    3. Fallback for <3 days history: Uses 30-day price median & previous_price heuristic without falsely rejecting new products.
    """
    try:
        if not product or not hasattr(product, "get"):
            return False

        current_mrp = float(product.get("mrp")) if product.get("mrp") is not None else None
        new_price = float(new_price) if new_price is not None else 0.0
        
        if not current_mrp or current_mrp <= 0 or new_price <= 0:
            return False

        platform = str(product.get("platform") or "").strip()
        platform_id = str(product.get("platform_id") or "").strip()

        # Step 1: Query local SQLite 14-day MRP history (Zero Cloud Egress)
        history = []
        if platform and platform_id:
            try:
                from budgetby import local_db
                history = await local_db.get_mrp_history(platform, platform_id, days=14)
            except Exception as e:
                logger.debug(f"Local MRP history lookup notice: {e}")

        # Extract valid historical MRP observations
        past_mrps = [float(h["mrp"]) for h in history if h.get("mrp") and float(h["mrp"]) > 0]

        if len(past_mrps) >= 3:
            baseline_median_mrp = statistics.median(past_mrps)
            baseline_min_mrp = min(past_mrps)

            # Rule 1: Sudden MRP Spike (>20% above historical median MRP)
            # If MRP was inflated to create an illusion of a deep discount
            if baseline_median_mrp > 0 and (current_mrp - baseline_median_mrp) / baseline_median_mrp > 0.20:
                # If the new price is essentially the same as historical normal (within 10% of median baseline)
                # or not genuinely discounted from normal MRP
                if new_price >= baseline_median_mrp * 0.85:
                    logger.info(
                        f"Fake discount flagged (Sudden MRP Spike): {platform}:{platform_id} "
                        f"MRP ₹{current_mrp} is >20% above median ₹{baseline_median_mrp:.1f}, price ₹{new_price}"
                    )
                    return True

            # Rule 2: Gradual MRP Creeping (>25% above 14-day min MRP)
            if baseline_min_mrp > 0 and (current_mrp - baseline_min_mrp) / baseline_min_mrp > 0.25:
                if new_price >= baseline_min_mrp * 0.90:
                    logger.info(
                        f"Fake discount flagged (Gradual MRP Creeping): {platform}:{platform_id} "
                        f"MRP ₹{current_mrp} crept >25% above min ₹{baseline_min_mrp:.1f}, price ₹{new_price}"
                    )
                    return True

            # If MRP is stable (within 5% of median) or returning to normal, it is genuine
            return False

        # Step 2: Fallback for products with insufficient history (< 3 days)
        # Avoid falsely rejecting new products; only flag obvious fake discount tricks
        median_30d = float(product.get("median_30d_price")) if product.get("median_30d_price") is not None else None
        previous_price = float(product.get("previous_price")) if product.get("previous_price") is not None else None

        if median_30d and median_30d > 0:
            # If price is within 10% of median selling price
            if abs(new_price - median_30d) / median_30d <= 0.10:
                if previous_price and previous_price < current_mrp:
                    if (current_mrp - previous_price) / previous_price > 0.20 and new_price >= previous_price * 0.95:
                        logger.info(
                            f"Fake discount flagged (Fallback heuristic): {platform}:{platform_id} "
                            f"Price ₹{new_price} near 30d median ₹{median_30d}, MRP inflated from ₹{previous_price} to ₹{current_mrp}"
                        )
                        return True

        return False
    except Exception as e:
        logger.error(f"Error in is_fake_discount: {e}")
        return False
