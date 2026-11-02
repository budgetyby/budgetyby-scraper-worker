"""
BudgetBy — Standalone Cloud Scraper Runner.
Runs 100% serverless/cloud-native (GitHub Actions, Render background, or Docker).
Zero dependency on local SQLite, zero dependency on local PC uptime.
"""

import sys
import os
import re
import asyncio
import logging
import datetime
from typing import Dict, Any

from budgetby import config, database
from budgetby.scrapers.amazon import AmazonScraper
from budgetby.scrapers.flipkart import FlipkartScraper
from budgetby.scrapers.myntra import MyntraScraper
from budgetby.scrapers.ajio import AjioScraper
from budgetby.scrapers.nykaa import NykaaScraper
from budgetby.engine.deal_detector import detect_deal
from budgetby.engine.deal_scorer import score_deal
from budgetby.scheduler.priority import assign_priority
from budgetby.affiliate.amazon_links import extract_asin, build_affiliate_url
from budgetby.affiliate.earnkaro_links import build_earnkaro_url_sync
from budgetby.affiliate.cuelinks_links import build_cuelinks_url
from budgetby.bot.templates import format_mega_deal, format_today_deal
from curl_cffi.requests import AsyncSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("budgetby.cloud_scraper")

scrapers = {
    "amazon": AmazonScraper(),
    "flipkart": FlipkartScraper(),
    "myntra": MyntraScraper(),
    "ajio": AjioScraper(),
    "nykaa": NykaaScraper(),
}

def is_valid_affiliate_shortlink(url: str, platform: str = "") -> bool:
    """Strict verification that a URL is an official monetized affiliate shortlink."""
    if not url:
        return False
    u = url.lower().strip()
    plat = (platform or "").lower().strip()
    if plat == "amazon":
        return ("dealpulse21-21" in u) or ("amzn.to" in u)
    elif plat == "flipkart":
        return ("fktr.in" in u) or ("ekaro.in" in u)
    elif plat == "myntra":
        return ("myntr.it" in u) or ("ekaro.in" in u)
    elif plat == "ajio":
        return ("ajiio.in" in u)
    elif plat == "nykaa":
        return ("clnk.in" in u)
    return any(domain in u for domain in ("fktr.in", "myntr.it", "ajiio.in", "clnk.in", "amzn.to", "dealpulse21-21"))


_telethon_client = None
_bot_conversation_lock = asyncio.Lock()

async def get_cloud_telethon_client():
    """Initializes a Telethon MTProto client in cloud environments using TELEGRAM_SESSION_STRING."""
    global _telethon_client
    if _telethon_client is not None:
        if _telethon_client.is_connected():
            return _telethon_client
        try:
            await _telethon_client.connect()
            if await _telethon_client.is_user_authorized():
                return _telethon_client
        except Exception as e:
            logger.debug(f"Telethon reconnect attempt note: {e}")
            _telethon_client = None

    session_str = (getattr(config, "TELEGRAM_SESSION_STRING", "") or os.environ.get("TELEGRAM_SESSION_STRING", "")).strip()
    api_id = getattr(config, "TG_API_ID", 24803975)
    api_hash = getattr(config, "TG_API_HASH", "551e18d6a36f5628543788220f8bb152")

    if not session_str:
        # Check if local session file exists
        if os.path.exists("telegram_user.session"):
            from telethon import TelegramClient
            _telethon_client = TelegramClient("telegram_user", int(api_id), str(api_hash))
            await _telethon_client.connect()
            if await _telethon_client.is_user_authorized():
                logger.info("Connected to Telethon MTProto via local session file.")
                return _telethon_client

    if session_str:
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            _telethon_client = TelegramClient(StringSession(session_str), int(api_id), str(api_hash))
            await _telethon_client.connect()
            if await _telethon_client.is_user_authorized():
                logger.info("Connected to Telethon MTProto via cloud TELEGRAM_SESSION_STRING.")
                return _telethon_client
            else:
                logger.warning("Cloud Telethon StringSession is not authorized.")
        except Exception as e:
            logger.warning(f"Failed to initialize cloud Telethon client: {e}")

    return None


async def convert_via_cloud_telegram_bots(url: str, platform: str) -> str | None:
    """Uses real EarnKaro / Cuelinks Telegram conversion bots to get official shortlinks."""
    client = await get_cloud_telethon_client()
    if not client:
        return None

    plat = (platform or "").lower().strip()
    async with _bot_conversation_lock:
        try:
            if plat in ("flipkart", "myntra", "ajio"):
                async with client.conversation("ekconverter9bot", timeout=12) as conv:
                    await conv.send_message(url)
                    resp = await conv.get_response()
                    txt = (resp.text or "").strip()
                    m = re.search(r'https?://[^\s]+', txt)
                    if m:
                        short_link = m.group(0)
                        if any(d in short_link.lower() for d in ("fktr.in", "myntr.it", "ajiio.in", "ekaro.in")):
                            return short_link
            elif plat == "nykaa":
                # Strip query parameters so CuelinksBot converts cleanly
                clean_nykaa = url.split("?")[0]
                async with client.conversation("cuelinks_bot", timeout=12) as conv:
                    sent_msg = await conv.send_message(clean_nykaa)
                    loop_start = asyncio.get_event_loop().time()
                    while (asyncio.get_event_loop().time() - loop_start) < 12:
                        rem_time = max(0.5, 12 - (asyncio.get_event_loop().time() - loop_start))
                        try:
                            resp = await conv.get_response(timeout=rem_time)
                        except (asyncio.TimeoutError, TimeoutError):
                            break
                        if not resp or not resp.text:
                            continue
                        if getattr(resp, "id", 0) < getattr(sent_msg, "id", 0):
                            continue
                        urls = re.findall(r'https?://[^\s\)\>]+', resp.text)
                        valid_links = [u.strip() for u in urls if "clnk.in" in u.lower()]
                        if valid_links:
                            return valid_links[0].rstrip(".,;!?")
        except Exception as e:
            logger.debug(f"Cloud Telegram bot link conversion error: {e}")
    return None


async def get_or_create_affiliate_link(prod: dict, force_fresh: bool = False) -> str | None:
    """
    Ensure product has a valid, monetized tracking link for cloud posting and clickouts.
    When force_fresh is True, bypasses database cached shortlinks and requests a live conversion
    from @ekconverter9bot (Flipkart/Myntra/Ajio) or @cuelinks_bot (Nykaa).
    Strictly returns None if the link is not an official affiliate shortlink.
    """
    platform = (prod.get("platform") or "").lower().strip()
    prod_url = prod.get("product_url") or ""
    aff_url = prod.get("affiliate_url") or ""
    pid = prod.get("id") or prod.get("product_id")

    if platform == "amazon":
        asin = extract_asin(prod_url) or extract_asin(aff_url)
        if asin:
            aff_link = build_affiliate_url(asin)
            if aff_link and pid:
                try:
                    await database.execute("UPDATE products SET affiliate_url = $1 WHERE id = $2;", aff_link, pid)
                except Exception:
                    pass
            return aff_link
        tag = getattr(config, "AMAZON_ASSOCIATE_TAG", "dealpulse21-21")
        target = prod_url or aff_url
        if target:
            sep = "&" if "?" in target else "?"
            return f"{target}{sep}tag={tag}"
        return None

    elif platform in ("flipkart", "myntra", "ajio"):
        # If already a valid shortlink and not force_fresh, reuse it
        if not force_fresh and aff_url and any(d in aff_url.lower() for d in ("fktr.in", "myntr.it", "ajiio.in", "ekaro.in")):
            return aff_url
        # Convert live via real Telegram bot @ekconverter9bot
        bot_link = await convert_via_cloud_telegram_bots(prod_url or aff_url, platform)
        if bot_link and any(d in bot_link.lower() for d in ("fktr.in", "myntr.it", "ajiio.in", "ekaro.in")):
            if pid:
                try:
                    await database.execute("UPDATE products SET affiliate_url = $1 WHERE id = $2;", bot_link, pid)
                except Exception:
                    pass
            return bot_link
        # Fallback 1: existing shortlink if live conversion timed out
        if aff_url and any(d in aff_url.lower() for d in ("fktr.in", "myntr.it", "ajiio.in", "ekaro.in")):
            return aff_url
        # Fallback 2: look up a verified shortlink for this platform in database catalog
        try:
            kw = "fktr.in" if platform == "flipkart" else ("myntr.it" if platform == "myntra" else "ajiio.in")
            row = await database.fetchrow(f"""
                SELECT affiliate_url FROM products 
                WHERE platform = '{platform}' AND affiliate_url LIKE '%{kw}%' AND in_stock = TRUE
                ORDER BY id DESC LIMIT 1;
            """)
            if row and row.get("affiliate_url"):
                return row["affiliate_url"]
        except Exception:
            pass
        return None

    elif platform == "nykaa":
        # If already a valid shortlink and not force_fresh, reuse it
        if not force_fresh and aff_url and "clnk.in" in aff_url.lower():
            return aff_url
        # Convert live via @cuelinks_bot
        bot_link = await convert_via_cloud_telegram_bots(prod_url or aff_url, platform)
        if bot_link and "clnk.in" in bot_link.lower():
            if pid:
                try:
                    await database.execute("UPDATE products SET affiliate_url = $1 WHERE id = $2;", bot_link, pid)
                except Exception:
                    pass
            return bot_link
        # Fallback 1: existing shortlink if live conversion timed out
        if aff_url and "clnk.in" in aff_url.lower():
            return aff_url
        # Fallback 2: look up a verified clnk.in shortlink for nykaa in database catalog
        try:
            row = await database.fetchrow("""
                SELECT affiliate_url FROM products 
                WHERE platform = 'nykaa' AND affiliate_url LIKE '%clnk.in%' AND in_stock = TRUE
                ORDER BY id DESC LIMIT 1;
            """)
            if row and row.get("affiliate_url"):
                return row["affiliate_url"]
        except Exception:
            pass
        return None

    return None


async def post_deal_to_telegram(product: dict, deal_result: dict) -> bool:
    """Posts a hot deal directly to Telegram channel via Bot API (zero local session needed)."""
    bot_token = getattr(config, "TELEGRAM_BOT_TOKEN", "").strip()
    channel_id = getattr(config, "TELEGRAM_CHANNEL_ID", "@deal_pulse_alerts").strip()

    if not bot_token or not channel_id:
        logger.warning("Telegram bot_token or channel_id not configured; skipping Telegram broadcast.")
        return False

    try:
        score = deal_result.get("deal_score", 0)
        badge = deal_result.get("badge", "")

        # Format message
        if score >= 75 or badge == "ATL":
            caption = format_mega_deal(product, deal_result)
        else:
            caption = format_today_deal(product, deal_result)

        image_url = product.get("image_url")
        api_base = f"https://api.telegram.org/bot{bot_token}"

        async with AsyncSession(timeout=10) as session:
            if image_url and image_url.startswith("http"):
                payload = {
                    "chat_id": channel_id,
                    "photo": image_url,
                    "caption": caption,
                    "parse_mode": "HTML"
                }
                resp = await session.post(f"{api_base}/sendPhoto", json=payload)
                if resp.status_code == 200:
                    logger.info(f"📢 [TELEGRAM POSTED] Deal for product {product.get('id')} to {channel_id}")
                    return True
                else:
                    # Fallback to text message if image fails to load in Telegram
                    logger.debug(f"sendPhoto failed ({resp.status_code}), falling back to sendMessage")

            payload = {
                "chat_id": channel_id,
                "text": caption,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            }
            resp = await session.post(f"{api_base}/sendMessage", json=payload)
            if resp.status_code == 200:
                logger.info(f"📢 [TELEGRAM POSTED] Deal for product {product.get('id')} to {channel_id}")
                return True
            else:
                logger.warning(f"Telegram sendMessage failed: {resp.status_code} - {resp.text[:100]}")
                return False
    except Exception as e:
        logger.warning(f"Failed to post deal to Telegram: {e}")
        return False


async def run_cloud_scrape_batch(limit: int = 30, concurrency: int = 5) -> Dict[str, Any]:
    """
    Fetches products due for check directly from Supabase,
    scrapes them concurrently, updates prices, records deals, and reschedules next_check.
    """
    start_time = datetime.datetime.now()
    pool = await database.init_pool()
    
    # 1. Fetch products due for check directly from Supabase
    products = await database.get_products_due_for_check(limit=limit)
    if not products:
        logger.info("✅ Zero products currently due for check in Supabase.")
        return {"checked": 0, "deals": 0, "price_changes": 0, "errors": 0}

    logger.info(f"🚀 [CLOUD SCRAPER] Starting cloud scrape for {len(products)} products due for check...")
    
    semaphore = asyncio.Semaphore(concurrency)
    stats = {
        "checked": 0,
        "price_changes": 0,
        "deals_created": 0,
        "atls": 0,
        "errors": 0
    }

    async def process_one(prod):
        platform = (prod.get("platform") or "").lower()
        url = prod.get("product_url")
        pid = prod.get("id")
        current_p = float(prod["current_price"]) if prod["current_price"] is not None else None

        scraper = scrapers.get(platform)
        if not scraper or not url:
            return

        async with semaphore:
            try:
                data = await scraper.scrape_product(url)
                if not data or data.get("error_code") in (503, 429):
                    logger.warning(f"⚠️ [{platform.upper()}] Block/Captcha for product {pid}. Delaying next check.")
                    await database.execute("""
                        UPDATE products SET next_check = NOW() + INTERVAL '4 hours' WHERE id = $1
                    """, pid)
                    stats["errors"] += 1
                    return

                new_p = float(data.get("current_price") or 0.0)
                in_stock = bool(data.get("in_stock", True))
                mrp_val = float(data.get("mrp") or prod.get("mrp") or (new_p if new_p > 0 else 0.0))

                if new_p <= 0 or not in_stock:
                    new_status = 'TEMP_OOS'
                    new_p = current_p or 0.0
                else:
                    new_status = 'ACTIVE'

                # Calculate new priority
                temp_prod = dict(prod)
                temp_prod["previous_price"] = current_p
                temp_prod["current_price"] = new_p
                temp_prod["mrp"] = mrp_val
                temp_prod["status"] = new_status
                temp_prod["last_price_change"] = datetime.datetime.now(datetime.timezone.utc) if (current_p and new_p != current_p) else None
                new_priority = assign_priority(temp_prod)
                interval_secs = config.PRIORITY_INTERVALS.get(new_priority, 48 * 3600)

                # Check if price changed
                price_changed = current_p is not None and new_p > 0 and new_p != current_p
                if price_changed:
                    stats["price_changes"] += 1

                # Update product in Supabase
                await database.execute("""
                    UPDATE products SET
                        previous_price = CASE WHEN $2 > 0 AND $2 != current_price THEN current_price ELSE previous_price END,
                        current_price = CASE WHEN $2 > 0 THEN $2 ELSE current_price END,
                        mrp = GREATEST(mrp, $3),
                        in_stock = $4,
                        status = $5,
                        priority_tier = $6,
                        all_time_low = CASE 
                            WHEN $2 > 0 AND (all_time_low IS NULL OR $2 < all_time_low) THEN $2 
                            ELSE all_time_low 
                        END,
                        last_price_change = CASE WHEN $2 > 0 AND $2 != current_price THEN NOW() ELSE last_price_change END,
                        last_checked = NOW(),
                        next_check = NOW() + make_interval(secs => $7)
                    WHERE id = $1;
                """, pid, new_p, mrp_val, in_stock, new_status, new_priority, interval_secs)

                # Update daily_prices snapshot for today
                if new_p > 0:
                    await database.upsert_daily_price(pid, new_p)

                # Affiliate link generation
                aff_link = await get_or_create_affiliate_link(temp_prod)
                if aff_link and aff_link != prod.get("affiliate_url"):
                    temp_prod["affiliate_url"] = aff_link
                    await database.execute("UPDATE products SET affiliate_url = $1 WHERE id = $2;", aff_link, pid)

                # Deal detection
                if new_status == 'ACTIVE' and new_p > 0:
                    deal = await detect_deal(temp_prod, new_p)
                    if deal:
                        score = score_deal(temp_prod, deal)
                        badge = deal.get("badge", "PRICE_DROP")
                        stats["deals_created"] += 1
                        if badge == "ATL":
                            stats["atls"] += 1
                        logger.info(f"🎯 [DEAL DETECTED] Product {pid}: {prod.get('title', '')[:35]} | ₹{new_p} (was ₹{current_p}) | {badge} | Score: {score}")

                        # Forward price drop to broadcaster platform queue to wait for its turn in the scheduled cycle
                        # Do NOT insert into deals table with posted_at=NOW() here; only broadcaster posts deals!
                        try:
                            from budgetby.broadcaster import queue_deal_for_platform
                            await queue_deal_for_platform(platform, {
                                "deal_id": 0,
                                "product_id": pid,
                                "posted_price": new_p,
                                "posted_mrp": mrp_val,
                                "deal_score": score,
                                "badge": badge,
                                "posted_at": None,
                                "title": prod.get("title", ""),
                                "platform": platform,
                                "product_url": prod.get("product_url", ""),
                                "affiliate_url": temp_prod.get("affiliate_url", ""),
                                "image_url": prod.get("image_url", ""),
                                "current_price": new_p,
                                "mrp": mrp_val,
                                "category": prod.get("category", ""),
                                "rating": prod.get("rating", 4.2)
                            })
                        except Exception as q_err:
                            logger.debug(f"Queue price drop note: {q_err}")

                stats["checked"] += 1
                logger.info(f"✓ Checked {platform.title()} product {pid}: ₹{new_p} (priority P{new_priority})")

            except Exception as e:
                logger.warning(f"Error checking product {pid} on {platform}: {e}")
                stats["errors"] += 1
                await database.execute("""
                    UPDATE products SET next_check = NOW() + INTERVAL '6 hours' WHERE id = $1
                """, pid)

    tasks = [process_one(p) for p in products]
    await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = (datetime.datetime.now() - start_time).total_seconds()
    logger.info(
        f"🏁 [CLOUD SCRAPER COMPLETE] Checked: {stats['checked']} | "
        f"Price Changes: {stats['price_changes']} | Deals: {stats['deals_created']} | "
        f"Errors: {stats['errors']} | Time: {elapsed:.1f}s"
    )
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BudgetBy Cloud Scraper Runner")
    parser.add_argument("--limit", type=int, default=25, help="Number of products to check in this batch")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent workers")
    args = parser.parse_args()

    asyncio.run(run_cloud_scrape_batch(limit=args.limit, concurrency=args.concurrency))
