"""
BudgetBy — Evergreen Deals
"""

import logging
import time
from asyncpg import Record
from budgetby import database, config

logger = logging.getLogger("budgetby.engine.evergreen")

def get_evergreen_cooldown_days(discount_pct: float) -> int:
    """Returns the cooldown days for evergreen deals."""
    return config.EVERGREEN_COOLDOWN_DAYS

_evergreen_cache: dict[str, tuple[list[Record], float]] = {}
_EVERGREEN_CACHE_TTL = 900.0  # Cache evergreen queries for 15 minutes in memory to minimize Supabase egress

async def find_evergreen_deals(limit: int = 10, platform: str = None) -> list[Record]:
    """Finds stable, highly discounted products that haven't been posted recently with memory caching."""
    cache_key = f"evergreen_{platform}_{limit}"
    now = time.monotonic()
    if cache_key in _evergreen_cache:
        rows, exp = _evergreen_cache[cache_key]
        if now < exp:
            return rows

    try:
        platform_filter = "AND platform = $1" if platform else ""
        query = f"""
            SELECT id, platform, product_url, affiliate_url, title, current_price, mrp, rating, review_count, image_url, category
            FROM products p
            WHERE p.in_stock = TRUE
              AND p.current_price > 0
              AND p.mrp > p.current_price
              AND NOT EXISTS (
                  SELECT 1 FROM deals d
                  WHERE d.product_id = p.id
                    AND d.posted_at > (NOW() - INTERVAL '24 hours')
              )
              {platform_filter}
            ORDER BY p.rating DESC NULLS LAST, p.id DESC
            LIMIT ${2 if platform else 1};
        """
        args = [platform] if platform else []
        args.append(limit * 3)
        raw_results = await database.fetch(query, *args)

        # Apply precautions and business filters in Python RAM (0 DB joins, 0 CPU overhead)
        min_disc = getattr(config, "EVERGREEN_MIN_MRP_DISCOUNT", 0.15)
        min_savings = getattr(config, "EVERGREEN_MIN_SAVINGS_INR", 100)

        filtered = []
        for r in raw_results:
            mrp = float(r["mrp"] or 0)
            cp = float(r["current_price"] or 0)
            if mrp > cp > 0:
                disc = (mrp - cp) / mrp
                savings = mrp - cp
                if min_disc <= disc <= 0.85 and savings >= min_savings:
                    filtered.append(r)
                    if len(filtered) >= limit:
                        break

        _evergreen_cache[cache_key] = (filtered, now + _EVERGREEN_CACHE_TTL)
        return filtered
    except Exception as e:
        logger.error(f"Error in find_evergreen_deals: {e}")
        return []

async def find_still_in_stock_reminders(limit: int = 5, platform: str = None) -> list[Record]:
    """Finds deals posted recently that are STILL in stock for reminders."""
    cache_key = f"reminders_{platform}_{limit}"
    now = time.monotonic()
    if cache_key in _evergreen_cache:
        rows, exp = _evergreen_cache[cache_key]
        if now < exp:
            return rows

    try:
        platform_filter = "AND p.platform = $1" if platform else ""
        query = f"""
            SELECT p.id, p.platform, p.product_url, p.affiliate_url, p.title, p.current_price, 
                   p.mrp, p.rating, p.review_count, p.image_url, p.category, 
                   d.posted_price, d.posted_at as initial_posted_at
            FROM deals d
            JOIN products p ON d.product_id = p.id
            WHERE p.in_stock = TRUE
              AND p.current_price > 0
              AND d.posted_at >= NOW() - INTERVAL '7 days'
              {platform_filter}
            ORDER BY d.posted_at ASC
            LIMIT ${2 if platform else 1};
        """
        args = [platform] if platform else []
        args.append(limit * 2)
        raw_results = await database.fetch(query, *args)

        filtered = []
        min_delay_secs = getattr(config, "MIN_REMINDER_DELAY_HOURS", 24) * 3600
        current_time = time.time()
        for r in raw_results:
            posted_at = r["initial_posted_at"]
            if posted_at:
                age_secs = current_time - posted_at.timestamp()
                if age_secs >= min_delay_secs:
                    filtered.append(r)
                    if len(filtered) >= limit:
                        break

        _evergreen_cache[cache_key] = (filtered, now + _EVERGREEN_CACHE_TTL)
        return filtered
    except Exception as e:
        logger.error(f"Error in find_still_in_stock_reminders: {e}")
        return []
