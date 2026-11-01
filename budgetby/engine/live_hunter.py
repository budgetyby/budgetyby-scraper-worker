"""
BudgetBy — Live Smart Deal Hunter (All Platforms)
Systematically scans live store high-discount APIs and deals feeds in strict round-robin ("one by one") order:
- Amazon: Today's Deals Hub & 27 Category Bestsellers/New Releases
- Flipkart: 97 Category feeds with exact SIDs (Mobiles, Electronics, Fashion, Home, Appliances, etc.)
- Myntra: 105 Category feeds with exact slugs (Men, Women, Kids, Ethnic, Western, Footwear, Accessories, etc.)
- Ajio: 14 Category feeds with exact numeric codes (Men, Women, Footwear, Home)
- Nykaa: 54 Category feeds with exact full paths (Skincare, Makeup, Haircare, Fragrances, Grooming, Appliances)

Upserts qualifying loot into PostgreSQL (expanding product count organically across all categories)
and returns the deal for immediate Telegram broadcasting on schedule.
"""

import asyncio
import random
import logging
from typing import Dict, Any, Optional
from budgetby import config
from budgetby import database
from budgetby.engine.cooldown import is_on_cooldown

logger = logging.getLogger("budgetby.engine.live_hunter")

# ════════════════════════════════════════════════════════════════════════
# 1. COMPREHENSIVE TARGET MAPPING (298 Total Categories Across 5 Stores)
# ════════════════════════════════════════════════════════════════════════

# Amazon: Deals Hub + 27 Category Bestsellers & New Releases
AMAZON_TARGETS = ["deals_hub", "deals_hub"] + list(getattr(config, "AMAZON_DISCOVERY_TARGETS", {}).keys())

# Flipkart: 97 Categories with exact SIDs and category mappings
FLIPKART_TARGETS = [
    (k, v["sid"], v.get("category", "fashion"))
    for k, v in getattr(config, "FLIPKART_DISCOVERY_TARGETS", {}).items()
]

# Myntra: 105 Categories with exact slugs
MYNTRA_TARGETS = [
    (k, v.get("category", "fashion"))
    for k, v in getattr(config, "MYNTRA_DISCOVERY_TARGETS", {}).items()
]

# Ajio: 14 Categories with exact numeric codes
AJIO_TARGETS = [
    (k, v["code"], v.get("category", "fashion"))
    for k, v in getattr(config, "AJIO_DISCOVERY_TARGETS", {}).items()
]

# Nykaa: 54 Categories with exact full paths
NYKAA_TARGETS = [
    (k, v["path"], v.get("category", "beauty"))
    for k, v in getattr(config, "NYKAA_DISCOVERY_TARGETS", {}).items()
]

# Stateful round-robin pointers (guarantees one-by-one sequential scanning without skipping any category)
_HUNT_INDICES = {
    "amazon": 0,
    "flipkart": 0,
    "myntra": 0,
    "ajio": 0,
    "nykaa": 0
}

async def hunt_live_store_deal(platform: str) -> Optional[Dict[str, Any]]:
    """
    Hunts for a single fresh, high-discount deal live from any store's live API / deals feed.
    Scrapes categories ONE BY ONE in strict sequential round-robin order.
    Upserts qualifying loot into PostgreSQL (expanding catalog organically) and returns it.
    If a category's products are on 24h cooldown or below discount threshold, seamlessly
    falls back to the next category in the round-robin queue (up to 4 attempts).
    """
    platform = platform.lower()
    min_disc = 0.25 if platform in ("nykaa", "amazon") else 0.35  # 25% for Amazon & Nykaa, 35% for fashion
    min_savings = 100 if platform == "nykaa" else 150

    MAX_ATTEMPTS = 4
    for attempt in range(MAX_ATTEMPTS):
        try:
            products = []
            
            # 1. AMAZON LIVE HUNT (Deals Hub + Category Deals in Sequential Round-Robin)
            if platform == "amazon":
                from budgetby.discovery import amazon_discover
                if not AMAZON_TARGETS:
                    return None
                idx = _HUNT_INDICES["amazon"] % len(AMAZON_TARGETS)
                _HUNT_INDICES["amazon"] += 1
                target = AMAZON_TARGETS[idx]
                
                logger.info(f"🏹 [LIVE HUNT] [AMAZON] Category {idx+1}/{len(AMAZON_TARGETS)}: {target} (attempt {attempt+1}/{MAX_ATTEMPTS})")
                if target == "deals_hub":
                    products = await amazon_discover.discover_deals_page(pages=2)
                else:
                    products = await amazon_discover.discover_category_deals(target, min_discount=35, pages=1)
                for p in products:
                    p["platform"] = "amazon"
                    p["category"] = p.get("category", target if target != "deals_hub" else "electronics")

            # 2. MYNTRA LIVE HUNT (105 Categories in Sequential Round-Robin)
            elif platform == "myntra":
                from budgetby.discovery import myntra_discover
                if not MYNTRA_TARGETS:
                    return None
                idx = _HUNT_INDICES["myntra"] % len(MYNTRA_TARGETS)
                _HUNT_INDICES["myntra"] += 1
                cat_slug, cat_label = MYNTRA_TARGETS[idx]
                
                logger.info(f"🏹 [LIVE HUNT] [MYNTRA] Category {idx+1}/{len(MYNTRA_TARGETS)}: {cat_slug} (attempt {attempt+1}/{MAX_ATTEMPTS})")
                products = await myntra_discover.discover_category(cat_slug, pages=1, sort="discount")
                for p in products:
                    p["platform"] = "myntra"
                    p["category"] = cat_label
                
            # 3. NYKAA LIVE HUNT (54 Categories in Sequential Round-Robin)
            elif platform == "nykaa":
                from budgetby.discovery import nykaa_discover
                if not NYKAA_TARGETS:
                    return None
                idx = _HUNT_INDICES["nykaa"] % len(NYKAA_TARGETS)
                _HUNT_INDICES["nykaa"] += 1
                cat_name, cat_path, cat_label = NYKAA_TARGETS[idx]
                
                logger.info(f"🏹 [LIVE HUNT] [NYKAA] Category {idx+1}/{len(NYKAA_TARGETS)}: {cat_name} ({cat_path}) (attempt {attempt+1}/{MAX_ATTEMPTS})")
                products = await nykaa_discover.discover_category(cat_path, pages=1, sort="discount")
                for p in products:
                    p["platform"] = "nykaa"
                    p["category"] = cat_label

            # 4. FLIPKART LIVE HUNT (97 Categories in Sequential Round-Robin)
            elif platform == "flipkart":
                from budgetby.discovery import flipkart_discover
                if not FLIPKART_TARGETS:
                    return None
                idx = _HUNT_INDICES["flipkart"] % len(FLIPKART_TARGETS)
                _HUNT_INDICES["flipkart"] += 1
                cat_name, sid, cat_label = FLIPKART_TARGETS[idx]
                
                logger.info(f"🏹 [LIVE HUNT] [FLIPKART] Category {idx+1}/{len(FLIPKART_TARGETS)}: {cat_name} (sid={sid}) (attempt {attempt+1}/{MAX_ATTEMPTS})")
                products = await flipkart_discover.discover_category(cat_name, sid, pages=1, sort="popularity")
                for p in products:
                    p["platform"] = "flipkart"
                    p["category"] = cat_label

            # 5. AJIO LIVE HUNT (14 Categories in Sequential Round-Robin)
            elif platform == "ajio":
                from budgetby.discovery import ajio_discover
                if not AJIO_TARGETS:
                    return None
                idx = _HUNT_INDICES["ajio"] % len(AJIO_TARGETS)
                _HUNT_INDICES["ajio"] += 1
                cat_name, code, cat_label = AJIO_TARGETS[idx]
                
                logger.info(f"🏹 [LIVE HUNT] [AJIO] Category {idx+1}/{len(AJIO_TARGETS)}: {cat_name} (code={code}) (attempt {attempt+1}/{MAX_ATTEMPTS})")
                products = await ajio_discover.discover_category(code, pages=1)
                for p in products:
                    p["platform"] = "ajio"
                    p["category"] = cat_label

            else:
                return None

            if not products:
                continue

            # Filter and pick the best non-cooldown candidate for posting (only winner gets upserted)
            random.shuffle(products)
            for p in products:
                price = float(p.get("current_price") or 0)
                mrp = float(p.get("mrp") or price)
                title = p.get("title") or ""

                if price <= 0 or mrp <= price or len(title) < 8:
                    continue

                disc = (mrp - price) / mrp
                if disc < min_disc or (mrp - price) < min_savings:
                    continue

                # Upsert into PostgreSQL (adds if new or updates existing)
                try:
                    pid = await database.upsert_product(p)
                    p["id"] = pid
                    
                    # Check cooldown
                    if await is_on_cooldown(pid):
                        continue

                    logger.info(f"🎯 [LIVE HUNT SUCCESS] [{platform.upper()}] Found fresh deal #{pid}: {title[:40]} | ₹{price} ({round(disc*100)}% OFF)")

                    badge = "LOOT" if disc >= 0.70 else ("MEGA_DROP" if disc >= 0.50 else "HOT_DEAL")
                    return {
                        "product": p,
                        "type": "flash_hunt",
                        "badge": badge,
                        "score": 85.0
                    }
                except Exception as ue:
                    logger.debug(f"Live hunt upsert note for {platform}: {ue}")

        except Exception as e:
            logger.debug(f"Live hunt exception for {platform} attempt {attempt}: {e}")

    return None


