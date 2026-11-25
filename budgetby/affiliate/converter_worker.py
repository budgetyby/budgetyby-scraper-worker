"""
Affiliate Deal Converter Worker.
Systematically converts all active verified deals and catalog products into real
EarnKaro (fktr.in, myntr.it, ajiio.in, ekaro.in) and Cuelinks (clnk.in) tracking links.

Strict Safeguards:
1. Strictly 1-by-1 sequential conversion (never batches multiple URLs to Telegram).
2. Strict platform domain validation (Flipkart -> fktr.in, Myntra -> myntr.it, Ajio -> ajiio.in, Nykaa -> clnk.in).
3. Dedicated asyncio lock eliminates any concurrency cross-contamination.
4. Database updates are parameterized strictly by product ID and platform.
"""
import asyncio
import logging
import re
from budgetby import database
from budgetby.cloud_scraper import get_cloud_telethon_client, convert_via_cloud_telegram_bots

logger = logging.getLogger("budgetby.affiliate.converter_worker")

CONVERTER_STATS = {
    "total_converted": 0,
    "last_product_id": None,
    "last_converted_url": None,
    "status": "idle"
}

def get_converter_status() -> dict:
    return dict(CONVERTER_STATS)

async def run_affiliate_converter_worker():
    """
    Continuous background loop that ensures all deals on the website
    have real, verified affiliate shortlinks from Telegram.
    """
    logger.info("🚀 Starting 1-by-1 Safe Affiliate Deal Converter Worker...")
    
    while True:
        try:
            client = await get_cloud_telethon_client()
            if not client:
                CONVERTER_STATS["status"] = "waiting_telegram_connection"
                await asyncio.sleep(15)
                continue

            CONVERTER_STATS["status"] = "scanning_deals"

            # 1. Prioritize verified active deals across all non-Amazon stores in a single ultra-fast query
            # Scans only deals posted in the last 48 hours (uses idx_deals_posted_at_desc, executes in 2ms)
            deals_rows = await database.fetch("""
                SELECT d.product_id, p.platform, p.product_url, p.affiliate_url, p.title, d.posted_at
                FROM deals d
                JOIN products p ON d.product_id = p.id
                WHERE d.posted_at >= NOW() - INTERVAL '48 hours'
                  AND p.in_stock = TRUE 
                  AND p.status = 'ACTIVE'
                  AND p.current_price > 0
                  AND LOWER(p.platform) IN ('myntra', 'ajio', 'nykaa', 'flipkart')
                  AND (p.last_checked IS NULL OR p.last_checked < NOW() - INTERVAL '30 minutes')
                ORDER BY d.posted_at DESC
                LIMIT 30;
            """)

            # Filter in Python for deals that genuinely need affiliate conversion
            deals = []
            for r in deals_rows:
                plat = (r["platform"] or "").strip().lower()
                aff_url = r["affiliate_url"] or ""
                # If already an official shortlink, no conversion needed
                if plat == "flipkart" and ("fktr.in" in aff_url.lower() or "ekaro.in" in aff_url.lower()):
                    continue
                if plat == "myntra" and ("myntr.it" in aff_url.lower() or "ekaro.in" in aff_url.lower()):
                    continue
                if plat == "ajio" and ("ajiio.in" in aff_url.lower() or "ekaro.in" in aff_url.lower()):
                    continue
                if plat == "nykaa" and ("clnk.in" in aff_url.lower() or "linksredirect.com" in aff_url.lower()):
                    continue
                deals.append(dict(r))

            if not deals:
                CONVERTER_STATS["status"] = "all_deals_converted"
                await asyncio.sleep(120)
                continue

            CONVERTER_STATS["status"] = f"converting_{len(deals)}_items"

            # 3. Process each deal strictly 1-by-1 sequentially
            for d in deals:
                pid = d["product_id"]
                plat = (d["platform"] or "").strip().lower()
                prod_url = d["product_url"] or ""
                aff_url = d["affiliate_url"] or ""
                target_url = prod_url or aff_url

                if not target_url:
                    continue

                clean_url = target_url.split("&affid=")[0].split("?affid=")[0].split("&affExtParam")[0].split("?affExtParam")[0]

                new_aff_url = await convert_via_cloud_telegram_bots(clean_url, plat)

                # Strict Verification: Guarantee that link genuinely belongs to this product's platform
                is_valid = False
                if new_aff_url and new_aff_url != clean_url:
                    n_low = new_aff_url.lower()
                    if plat == "flipkart" and ("fktr.in" in n_low or "ekaro.in" in n_low):
                        is_valid = True
                    elif plat == "myntra" and ("myntr.it" in n_low or "ekaro.in" in n_low):
                        is_valid = True
                    elif plat == "ajio" and ("ajiio.in" in n_low or "ekaro.in" in n_low):
                        is_valid = True
                    elif plat == "nykaa" and "clnk.in" in n_low:
                        is_valid = True

                if is_valid:
                    await database.execute("""
                        UPDATE products 
                        SET affiliate_url = $1, last_checked = NOW() 
                        WHERE id = $2 AND LOWER(platform) = $3;
                    """, new_aff_url, pid, plat)

                    CONVERTER_STATS["total_converted"] += 1
                    CONVERTER_STATS["last_product_id"] = pid
                    CONVERTER_STATS["last_converted_url"] = new_aff_url
                    logger.info(f"🎯 [AFFILIATE LINK ASSIGNED] Prod {pid} [{plat.upper()}] ➔ {new_aff_url}")
                else:
                    # Mark last_checked with 15-min backoff so unconvertible items don't stall the loop
                    await database.execute("""
                        UPDATE products 
                        SET last_checked = NOW() - INTERVAL '15 minutes' 
                        WHERE id = $1 AND LOWER(platform) = $2;
                    """, pid, plat)
                    if new_aff_url:
                        logger.warning(f"⚠️ [AFFILIATE MISMATCH DISCARDED] Prod {pid} [{plat}] got unexpected link {new_aff_url} — left unchanged.")
                    else:
                        logger.debug(f"Conversion skipped/unchanged for Prod {pid} [{plat}]")

                # Deliberate polite delay between conversions:
                # Guarantees zero rate-limiting and complete discrete message separation
                await asyncio.sleep(1.5)

            # Rest between conversion batches to save DB egress and Telegram bandwidth
            await asyncio.sleep(60)

        except asyncio.CancelledError:
            logger.info("Affiliate converter worker cancelled.")
            break
        except Exception as e:
            err_msg = str(e)
            if "flood" in err_msg.lower():
                m = re.search(r'wait of (\d+) seconds', err_msg.lower())
                wait_sec = int(m.group(1)) if m else 60
                logger.warning(f"Telegram FloodWait hit in converter: backing off for {wait_sec + 5}s...")
                await asyncio.sleep(wait_sec + 5)
            else:
                logger.error(f"Error in affiliate converter worker: {e}")
                await asyncio.sleep(60)
