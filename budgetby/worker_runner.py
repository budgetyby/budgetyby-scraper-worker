"""
BudgetBy — 24/7 Autonomous Continuous Cloud Worker Runner.
Designed to run inside GitHub Actions (Public Worker Repo = Unlimited 100% Free Runner Minutes).

Capabilities:
1. 30-Second Continuous Deal Broadcaster:
   - Posts next verified high-discount deal/price-drop to Telegram @deal_pulse_alerts every 30 seconds.
2. Continuous Price Checking & Scrape Loop:
   - Periodically rechecks existing database products for real price changes.
   - Discovers hot new deals from Amazon, Flipkart, Ajio, Myntra, Nykaa.
3. Keep-Alive Ping:
   - Pings Render web storefront (https://budgetyby.onrender.com/healthz) every 2 minutes so Render stays awake 24/7.
4. Auto-Chaining Self-Relay:
   - Before runner 6-hour limit expires, dispatches next GitHub Action run to ensure 24/7 perpetual uptime.
"""
import os
import sys
import time
import asyncio
import logging
from datetime import datetime, timezone
from curl_cffi.requests import AsyncSession

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("budgetby.worker_runner")

async def keep_alive_loop(duration_secs: int):
    """Pings Render storefront every 120s to ensure Render never spins down."""
    url = "https://budgetyby.onrender.com/healthz"
    end_time = time.time() + duration_secs
    while time.time() < end_time:
        try:
            async with AsyncSession(timeout=10) as session:
                r = await session.get(url)
                logger.info(f"🌐 [KEEP-ALIVE] Render storefront pinged: {r.status_code}")
        except Exception as e:
            logger.debug(f"[KEEP-ALIVE] Notice: {e}")
        await asyncio.sleep(120)

async def periodic_scraper_loop(duration_secs: int):
    """Periodically rechecks prices of database products & searches for new deals."""
    from budgetby.cloud_scraper import run_cloud_scrape_batch
    end_time = time.time() + duration_secs
    # Run first scrape batch after 15 seconds warm-up
    await asyncio.sleep(15)

    while time.time() < end_time:
        try:
            logger.info("🔍 [SCRAPER LOOP] Starting cloud price verification batch (40 products)...")
            stats = await run_cloud_scrape_batch(limit=40, concurrency=5)
            logger.info(f"✅ [SCRAPER BATCH DONE] Scraped={stats.get('scraped')}, PriceDrops={stats.get('price_drops')}, Deals={stats.get('deals_found')}")

            # 2. Live Store Flash Hunter - Discover brand-new deals across all 5 stores and queue for broadcast
            try:
                logger.info("🏹 [FLASH HUNTER] Scanning live store deals feeds across stores...")
                from budgetby.engine.live_hunter import hunt_live_store_deal
                from budgetby.broadcaster import queue_deal_for_platform, _last_posted_product_ids
                from budgetby.engine.cooldown import is_on_cooldown
                for s in ["myntra", "flipkart", "amazon", "ajio", "nykaa"]:
                    try:
                        h_res = await asyncio.wait_for(hunt_live_store_deal(s), timeout=15)
                        if h_res and h_res.get("product"):
                            hp = h_res["product"]
                            pid = hp.get("id")
                            if pid and pid not in _last_posted_product_ids and not await is_on_cooldown(pid):
                                deal_payload = {
                                    "deal_id": 0,
                                    "product_id": pid,
                                    "posted_price": hp.get("current_price"),
                                    "posted_mrp": hp.get("mrp"),
                                    "deal_score": h_res.get("score", 85.0),
                                    "badge": h_res.get("badge", "HOT_DEAL"),
                                    "posted_at": None,
                                    "title": hp.get("title"),
                                    "platform": s,
                                    "product_url": hp.get("product_url"),
                                    "affiliate_url": hp.get("affiliate_url"),
                                    "image_url": hp.get("image_url"),
                                    "current_price": hp.get("current_price"),
                                    "mrp": hp.get("mrp"),
                                    "category": hp.get("category"),
                                    "rating": hp.get("rating")
                                }
                                await queue_deal_for_platform(s, deal_payload)
                                logger.info(f"✨ [NEW DEAL QUEUED] {s.upper()}: #{pid} {hp.get('title', '')[:35]}")
                    except Exception as he:
                        logger.debug(f"Hunter note for {s}: {he}")
            except Exception as e:
                logger.debug(f"Flash hunter loop note: {e}")
        except Exception as e:
            logger.error(f"❌ [SCRAPER BATCH ERROR] {e}")

        # Re-check database every 180 seconds (3 minutes)
        await asyncio.sleep(180)

async def self_relay_trigger():
    """Triggers the next GitHub Action workflow run before the current one terminates."""
    gh_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_PAT")
    repo = os.getenv("GITHUB_REPOSITORY", "budgetyby/budgetyby-scraper-worker")
    if not gh_token:
        logger.warning("No GITHUB_TOKEN available for self-relay trigger; relying on cron.")
        return

    logger.info(f"🔄 [AUTO-RELAY] Dispatching next GitHub Actions workflow run for {repo}...")
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {gh_token}",
        "User-Agent": "BudgetBy-Worker"
    }
    url = f"https://api.github.com/repos/{repo}/actions/workflows/cloud_scraper.yml/dispatches"
    try:
        async with AsyncSession(timeout=15) as session:
            resp = await session.post(url, headers=headers, json={"ref": "main"})
            if resp.status_code in (201, 204):
                logger.info("🎉 [AUTO-RELAY] Next 24/7 worker workflow dispatched successfully!")
            else:
                logger.warning(f"[AUTO-RELAY] Dispatch returned {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error(f"[AUTO-RELAY] Error dispatching next run: {e}")

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="BudgetBy 24/7 Cloud Worker")
    parser.add_argument("--duration-minutes", type=int, default=330, help="Run duration in minutes (default: 330 = 5.5 hours)")
    args = parser.parse_args()

    duration_secs = args.duration_minutes * 60
    logger.info(f"🚀 Starting BudgetBy 24/7 Cloud Worker (Duration: {args.duration_minutes} minutes)...")

    # Initialize shared database connection pool
    from budgetby import database
    await database.init_pool()

    # Start the 30-second broadcaster
    from budgetby.broadcaster import run_paced_30s_broadcaster, run_continuous_deal_hunter_daemon
    broadcaster_task = asyncio.create_task(run_paced_30s_broadcaster())

    # Start the continuous deal hunter daemon across all 298 category feeds
    hunter_task = asyncio.create_task(run_continuous_deal_hunter_daemon())

    # Start Render keep-alive
    keepalive_task = asyncio.create_task(keep_alive_loop(duration_secs))

    # Start Scraper Loop
    scraper_task = asyncio.create_task(periodic_scraper_loop(duration_secs))

    # Wait until duration is almost reached (reserve 10 mins for clean relay)
    sleep_time = max(10, duration_secs - 600)
    logger.info(f"⏳ Main worker active. Self-relay scheduled in {sleep_time/60:.1f} minutes.")
    await asyncio.sleep(sleep_time)

    # Trigger relay for perpetual execution
    await self_relay_trigger()

    # Finish remaining 10 minutes
    await asyncio.sleep(min(600, duration_secs))
    logger.info("Worker loop finished gracefully.")

if __name__ == "__main__":
    asyncio.run(main())
