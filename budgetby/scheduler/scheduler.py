"""
BudgetBy — Scheduler
Sets up all recurring jobs using APScheduler.
"""

import logging
import asyncio
import time
import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from budgetby import config, database
from budgetby.scheduler import cleanup

logger = logging.getLogger("budgetby.scheduler")

_scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

_bot = None
_total_scanned_count = 0
_minute_scanned_count = 0
_last_scan_stat_time = time.time()

def set_bot(bot):
    global _bot
    _bot = bot


async def price_check_loop():
    """
    Core loop: grab products from local SQLite, scrape, detect deals, route Hot/Cold path.
    Runs every 30 seconds.
    """
    try:
        from budgetby.scrapers.amazon import AmazonScraper
        from budgetby.scrapers.flipkart import FlipkartScraper
        from budgetby.scrapers.myntra import MyntraScraper
        from budgetby.scrapers.ajio import AjioScraper
        from budgetby.scrapers.nykaa import NykaaScraper
        from budgetby.engine.deal_detector import detect_deal
        from budgetby.engine.deal_scorer import score_deal
        from budgetby.engine.fake_discount import is_fake_discount
        from budgetby.engine.cooldown import is_on_cooldown
        from budgetby.scheduler.priority import assign_priority
        from budgetby.engine.posting_queue import get_posting_queue
        from budgetby import local_db

        scrapers = {
            "amazon": AmazonScraper(),
            "flipkart": FlipkartScraper(),
            "myntra": MyntraScraper(),
            "ajio": AjioScraper(),
            "nykaa": NykaaScraper(),
        }

        # Pull from Local DB (Zero Read Egress)
        products = await local_db.get_products_due_for_check(limit=40)
        if not products:
            logger.info("🔍 [LOCAL SCANNER] 0 products currently due for check.")
            return

        logger.info(f"🔍 [LOCAL SCANNER] Checking batch of {len(products)} products from local SQLite DB...")
            
        import asyncio
        semaphore = asyncio.Semaphore(config.SCRAPER_WORKERS)
        
        async def process_product(prod):
            platform = prod["platform"]
            pid = prod["platform_id"]
            if not local_db.mark_in_flight(platform, pid):
                return
            try:
                async with semaphore:
                    scraper = scrapers.get(platform)
                    if not scraper:
                        return

                    try:
                        data = await scraper.scrape_product(prod["url"])
                        # Captcha / Error 503 Protection
                        if not data or data.get("error_code") in (503, 429):
                            logger.warning(f"Captcha/Block detected for {prod['url']}. Retrying later.")
                            local_db.update_product_check_time(platform, pid, prod["priority_tier"])
                            return

                        new_p = float(data.get("current_price", 0.0))
                        in_stock = data.get("in_stock", True)
                        
                        if new_p <= 0 or not in_stock:
                            new_status = 'TEMP_OOS'
                            new_p = prod["current_price"] # Keep old price for reference
                        else:
                            new_status = 'ACTIVE'

                        # Determine new priority
                        temp_prod = dict(prod)
                        temp_prod['previous_price'] = prod["current_price"]
                        temp_prod['current_price'] = new_p
                        temp_prod['mrp'] = data.get("mrp") or prod.get("mrp") or new_p
                        temp_prod['status'] = new_status
                        temp_prod['last_price_change'] = datetime.datetime.now(datetime.timezone.utc) if new_p != prod["current_price"] else None
                        new_priority = assign_priority(temp_prod)

                        # Update local check time immediately
                        local_db.update_product_check_time(platform, pid, new_priority)
                        
                        # Record daily MRP observation in local SQLite (Zero Cloud Egress)
                        observed_mrp = data.get("mrp") or prod.get("mrp")
                        if observed_mrp and float(observed_mrp) > 0:
                            local_db.record_mrp_observation(platform, pid, float(observed_mrp))

                        # Did price change or stock status change?
                        if new_p != prod["current_price"] or new_status != prod["status"]:
                            # 1. Hot Path (Tier 1 & 2) -> Instant Supabase Update
                            if new_priority in (1, 2):
                                # Update local DB first
                                local_db.update_product_locally(
                                    platform, pid, prod["url"], new_p, 
                                    data.get("mrp") or prod["mrp"], new_priority, new_status
                                )
                                # Instantly send to Supabase (Zero Egress, Free Ingress)
                                await database.execute("""
                                    UPDATE products SET current_price = $1, status = $2, priority_tier = $3, in_stock = $4, last_checked = NOW()
                                    WHERE platform = $5 AND platform_id = $6
                                """, new_p, new_status, new_priority, in_stock, platform, pid)
                            
                            # 2. Cold Path (Tier 3 & 4) -> Queue in Local DB
                            else:
                                local_db.queue_pending_sync(platform, pid, new_p, in_stock, new_status)
                                
                            # Deal Detection (detect_deal checks fake discount internally)
                            if new_status == 'ACTIVE':
                                deal = await detect_deal(temp_prod, new_p)
                                if deal:
                                    score = score_deal(temp_prod, deal)
                                    badge = deal.get("badge", "PRICE_DROP")
                                    discount_val = deal.get("discount_pct", 0)
                                    # Only queue to Telegram if it meets deal threshold (Score >= 50 or discount >= 15% or ATL/LOOT)
                                    # Otherwise it is already saved to DB/catalog for website shoppers without choking Telegram queue
                                    if score >= 50 or discount_val >= 15 or badge in ["ATL", "LOOT", "MEGA_DROP"]:
                                        deal_data = {
                                            "product": {
                                                "id": None,
                                                "platform": platform,
                                                "platform_id": pid,
                                                "title": data.get("title") or prod.get("title") or f"{platform.title()} Product",
                                                "url": prod["url"],
                                                "product_url": prod["url"],
                                                "affiliate_url": data.get("affiliate_url") or prod.get("affiliate_url", ""),
                                                "image_url": data.get("image_url") or prod.get("image_url", ""),
                                                "current_price": new_p,
                                                "mrp": data.get("mrp") or prod.get("mrp") or new_p,
                                                "rating": data.get("rating") or prod.get("rating", 0.0),
                                                "review_count": data.get("review_count") or prod.get("review_count", 0),
                                                "category": prod.get("category") or data.get("category") or "general",
                                            },
                                            "type": deal.get("deal_type", "price_drop"),
                                            "badge": badge,
                                            "score": score,
                                            "source_channel": "scheduler"
                                        }
                                        # Add to Telegram Queue for immediate delivery
                                        await get_posting_queue().queue_deal(deal_data)

                    except Exception as e:
                        logger.error(f"Error checking {prod['url']}: {e}")
                        local_db.update_product_check_time(platform, pid, prod["priority_tier"])
            finally:
                local_db.release_in_flight(platform, pid)

        await asyncio.gather(*(process_product(dict(p)) for p in products))

        global _total_scanned_count, _minute_scanned_count, _last_scan_stat_time
        _total_scanned_count += len(products)
        _minute_scanned_count += len(products)
        now_ts = time.time()
        if now_ts - _last_scan_stat_time >= 60:
            rate_per_min = _minute_scanned_count
            logger.info(f"📊 [SCAN PROGRESS] Checked {rate_per_min} products in the last minute | Total scanned this session: {_total_scanned_count:,} products (Zero Supabase Egress)")
            _minute_scanned_count = 0
            _last_scan_stat_time = now_ts
        else:
            logger.info(f"✅ [LOCAL SCANNER] Batch of {len(products)} products checked. Total session scans: {_total_scanned_count:,}")

    except Exception as e:
        logger.error(f"Error in price_check_loop: {e}")

async def nightly_batch_sync():
    """10:00 PM Batch Sync for Tier 3/4 Cold Path updates."""
    try:
        from budgetby import local_db
        logger.info("Starting 10:00 PM nightly batch sync...")
        
        # 1. Grab all queued updates and clear local outbox
        rows = await local_db.get_and_clear_pending_syncs()
        if rows:
            logger.info(f"Uploading {len(rows)} batched price updates to Supabase...")
            
            # 2. Chunk updates in batches of 500
            updates = []
            for r in rows:
                updates.append((r["new_price"], r["in_stock"], r["status"], r["platform"], r["platform_id"]))
                
                if len(updates) >= 500:
                    await database.batch_update_products(updates)
                    updates = []
            
            # Flush remaining
            if updates:
                await database.batch_update_products(updates)
                
        # 3. Take Midnight Snapshot instantly after sync is guaranteed finished
        await database.sync_daily_price_baselines()
        logger.info("Nightly batch sync and snapshot completed successfully.")
    except Exception as e:
        logger.error(f"Error in nightly_batch_sync: {e}")


async def discovery_job():
    """Run full product discovery across all platforms."""
    try:
        from budgetby.discovery.seeder import ProductSeeder
        logger.info("Running full discovery...")
        seeder = ProductSeeder()
        stats = await seeder.run_full_discovery()
        logger.info(f"Discovery complete: {stats}")
    except Exception as e:
        logger.error(f"Error in discovery_job: {e}")


async def morning_digest():
    """Curate and post Top 5 Morning Deals Digest at 9:00 AM IST."""
    await _post_digest(digest_type="Morning", hours_lookback=12)


async def evening_digest():
    """Curate and post Top 5 Evening Deals Digest at 8:00 PM IST."""
    await _post_digest(digest_type="Evening", hours_lookback=12)


async def _post_digest(digest_type: str, hours_lookback: int = 12):
    try:
        if not _bot:
            logger.warning(f"Cannot send {digest_type} digest: bot not initialized.")
            return

        from budgetby.bot.templates import format_daily_digest
        
        # Query top 5 deals in the lookback window ordered by deal_score and savings_pct
        rows = await database.fetch("""
            SELECT p.title, p.platform, p.category, p.product_url, p.affiliate_url,
                   p.image_url, p.rating, p.review_count, d.posted_price, d.posted_mrp,
                   d.savings_amount, d.savings_pct, d.deal_score, d.badge
            FROM deals d
            JOIN products p ON d.product_id = p.id
            WHERE d.posted_at >= NOW() - ($1 * INTERVAL '1 hour')
            ORDER BY d.deal_score DESC, d.savings_pct DESC
            LIMIT 5;
        """, hours_lookback)

        if not rows or len(rows) < 3:
            # Fallback to top products with highest discount currently in DB
            rows = await database.fetch("""
                SELECT title, platform, category, product_url, affiliate_url,
                       image_url, rating, review_count, current_price as posted_price,
                       mrp as posted_mrp, (mrp - current_price) as savings_amount,
                       ((mrp - current_price)/mrp) as savings_pct, 80 as deal_score, 'ATL' as badge
                FROM products
                WHERE current_price > 0 AND mrp > current_price AND in_stock = TRUE AND rating >= 4.0
                ORDER BY ((mrp - current_price)/mrp) DESC, review_count DESC
                LIMIT 5;
            """)

        if not rows:
            return

        deals_list = [dict(r) for r in rows]
        text = format_daily_digest(deals_list, digest_type=digest_type)

        await _bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=False
        )
        logger.info(f"✅ Successfully posted {digest_type} deals digest to {config.TELEGRAM_CHANNEL_ID}")
    except Exception as e:
        logger.error(f"Error posting {digest_type} deals digest: {e}")


async def paced_posting_loop():
    """
    Continuous 30-Second Deal Broadcaster:
    - If the posting queue has intercepted channel deals: fast-drains them at 8s cadence
      (no lock, no live hunt, no pre-flight scraping — already verified).
    - If the queue is empty: hunts live deals from store APIs, then falls back to
      evergreen catalog with pre-flight verification.
    """
    try:
        from budgetby.engine.posting_queue import get_posting_queue
        pq = get_posting_queue()
        await pq.process_queue(_bot)
    except Exception as e:
        logger.error(f"Error in paced_posting_loop: {e}")


async def verify_active_deals_loop():
    """
    Targeted Micro-Job Deal Verifier:
    Fires every 60 seconds to re-verify the oldest unchecked active deals (3 products/min).
    Ensures that if retailer raises price back to MRP or item sells out,
    the deal is immediately dropped from the consumer website.
    Uses ultra-low background resources (< 0.1% CPU, ~3 requests/min across 5 stores).
    """
    try:
        from budgetby.scrapers.amazon import AmazonScraper
        from budgetby.scrapers.flipkart import FlipkartScraper
        from budgetby.scrapers.myntra import MyntraScraper
        from budgetby.scrapers.ajio import AjioScraper
        from budgetby.scrapers.nykaa import NykaaScraper

        scrapers = {
            "amazon": AmazonScraper(),
            "flipkart": FlipkartScraper(),
            "myntra": MyntraScraper(),
            "ajio": AjioScraper(),
            "nykaa": NykaaScraper(),
        }

        candidates = await database.fetch("""
            SELECT p.id, p.platform, p.product_url, p.priority_tier, 
                   COALESCE(d.posted_price, p.current_price) as posted_price, 
                   p.current_price
            FROM products p
            JOIN deals d ON d.product_id = p.id
            WHERE p.in_stock = TRUE AND p.status = 'ACTIVE'
              AND d.posted_at >= NOW() - INTERVAL '72 hours'
            ORDER BY p.last_checked ASC NULLS FIRST
            LIMIT 5;
        """)
        if not candidates:
            return

        for prod in candidates:
            platform = prod["platform"]
            scraper = scrapers.get(platform)
            if not scraper:
                continue
            try:
                data = await scraper.scrape_product(prod["product_url"])
                if not data or not data.get("current_price") or data.get("in_stock") is False:
                    # Verified out of stock or unavailable
                    await database.execute(
                        "UPDATE products SET in_stock = FALSE, last_checked = NOW() WHERE id = $1;",
                        prod["id"]
                    )
                    logger.info(f"Micro-verifier: product #{prod['id']} ({platform}) marked OUT OF STOCK")
                else:
                    new_p = float(data["current_price"])
                    await database.execute("""
                        UPDATE products SET 
                            current_price = $2,
                            mrp = COALESCE($3, mrp),
                            in_stock = $4,
                            last_checked = NOW()
                        WHERE id = $1;
                    """, prod["id"], new_p, data.get("mrp"), data.get("in_stock", True))
                    if prod["posted_price"] and new_p > float(prod["posted_price"]) * 1.01:
                        logger.info(f"Micro-verifier: price increased for #{prod['id']} ({platform}) from {prod['posted_price']} to {new_p}")
            except Exception as e:
                logger.debug(f"Micro-verifier scrape error for #{prod['id']}: {e}")
                await database.execute("UPDATE products SET last_checked = NOW() WHERE id = $1;", prod["id"])
    except Exception as e:
        logger.error(f"Error in verify_active_deals_loop: {e}")


def start_scheduler():
    """Configure and start all scheduled jobs."""
    if _scheduler.running:
        logger.info("Scheduler already running, skipping start.")
        return
    logger.info("Starting scheduler...")

    # Core catalog price checking — every 120 seconds (15 products / 2 min)
    
    # Core catalog price checking (Offloaded to 24/7 Cloud by default)
    if config.ENABLE_LOCAL_SCRAPERS:
        _scheduler.add_job(price_check_loop, "interval", seconds=30, id="price_check",
                           max_instances=1, coalesce=True, misfire_grace_time=60, replace_existing=True)
        logger.info("Local price check loop scheduled (30s interval).")
    else:
        logger.info("ℹ️ Local price check loop disabled: price scraping is offloaded to 24/7 Cloud runners.")

    # 10 PM Batch Sync (Cold Path)
    _scheduler.add_job(nightly_batch_sync, "cron", hour=22, minute=0, timezone="Asia/Kolkata", id="nightly_batch_sync", misfire_grace_time=300, replace_existing=True)
    
    # Weekly SQLite Vacuum (Defragmentation)
    from budgetby import local_db
    _scheduler.add_job(local_db.vacuum_db, "cron", day_of_week="sun", hour=23, minute=0, timezone="Asia/Kolkata", id="sqlite_vacuum", misfire_grace_time=300, replace_existing=True)


    # Targeted Micro-Job Deal Verifier — every 180 seconds (3 minutes)
    _scheduler.add_job(verify_active_deals_loop, "interval", seconds=180, id="deal_verifier",
                       max_instances=1, coalesce=True, misfire_grace_time=30, replace_existing=True)

    # Discovery — every 6 hours (Amazon/Flipkart/Ajio/Nykaa only)
    _scheduler.add_job(discovery_job, "interval",
                       hours=config.DISCOVERY_INTERVAL_HOURS, id="discovery", max_instances=1, misfire_grace_time=30, replace_existing=True)

    # Daily cleanup at midnight IST
    _scheduler.add_job(cleanup.daily_cleanup, "cron", hour="0,12", minute=0, id="daily_cleanup", misfire_grace_time=43200, replace_existing=True)

    # Monthly benchmark shift on the 1st
    _scheduler.add_job(cleanup.monthly_maintenance, "cron", day=1, hour=1, minute=0,
                       id="monthly_maintenance", misfire_grace_time=300, replace_existing=True)

    # Bi-weekly backup on 1st and 15th of each month at 3:00 AM IST (saves 93% egress)
    if config.BACKUP_ENABLED:
        _scheduler.add_job(cleanup.run_backup, "cron", day="1,15",
                           hour=config.BACKUP_HOUR, minute=0, id="biweekly_backup", misfire_grace_time=300, replace_existing=True)

    # Daily Morning Digest at 9:00 AM IST
    _scheduler.add_job(morning_digest, "cron", hour=9, minute=0, timezone="Asia/Kolkata", id="morning_digest", misfire_grace_time=300, replace_existing=True)

    # Daily Evening Digest at 8:00 PM IST
    _scheduler.add_job(evening_digest, "cron", hour=20, minute=0, timezone="Asia/Kolkata", id="evening_digest", misfire_grace_time=300, replace_existing=True)

    # High-Velocity 30-Second Paced Broadcaster with Live Deal Hunter
    _scheduler.add_job(paced_posting_loop, "interval", seconds=30, id="paced_posting",
                       max_instances=1, coalesce=True, misfire_grace_time=60, replace_existing=True)

    # Public Telegram Deal Channels Monitor (DoH) — every 90 seconds
    try:
        from budgetby.ingest.channel_monitor import run_channel_monitor
        _scheduler.add_job(run_channel_monitor, "interval", seconds=90, id="channel_monitor",
                           max_instances=1, coalesce=True, misfire_grace_time=30, replace_existing=True)
    except Exception as e:
        logger.warning(f"Could not schedule channel_monitor: {e}")

    # Periodic Cooldown Cleanup — every 2 hours
    try:
        from budgetby.engine.cooldown import cleanup_expired_cooldowns
        _scheduler.add_job(cleanup_expired_cooldowns, "interval", hours=2, id="cooldown_cleanup",
                           max_instances=1, coalesce=True, misfire_grace_time=60, replace_existing=True)
    except Exception as e:
        logger.warning(f"Could not schedule cooldown_cleanup: {e}")

    _scheduler.start()
    logger.info("Scheduler started with all jobs configured")


def stop_scheduler(wait: bool = True):
    """Gracefully stop the scheduler."""
    logger.info("Stopping scheduler...")
    if _scheduler.running:
        _scheduler.shutdown(wait=wait)
        logger.info("Scheduler stopped")
