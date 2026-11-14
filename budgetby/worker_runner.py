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
            logger.info("🔍 [SCRAPER LOOP] Starting cloud price verification batch (25 products)...")
            stats = await run_cloud_scrape_batch(limit=25, concurrency=5)
            logger.info(f"✅ [SCRAPER BATCH DONE] Scraped={stats.get('scraped')}, PriceDrops={stats.get('price_drops')}, Deals={stats.get('deals_found')}")
        except Exception as e:
            logger.error(f"❌ [SCRAPER BATCH ERROR] {e}")

        # Paced re-check interval: 300 seconds (5 minutes) to protect Supabase network egress
        await asyncio.sleep(300)

async def cloud_maintenance_loop(duration_secs: int):
    """
    Periodic Cloud Database Maintenance Loop:
    Guarantees perpetual 100% free-tier database compliance (<300 MB):
    1. Prunes price history older than 5 days.
    2. Deletes expired post cooldown records.
    3. Deletes ingested telegram channel records older than 14 days.
    4. Cleans expired deal tracking records.
    5. Runs VACUUM ANALYZE to compact disk blocks and update query planner stats.
    """
    from budgetby import database, config
    end_time = time.time() + duration_secs
    # Run first maintenance cycle 30 seconds after worker start
    await asyncio.sleep(30)

    while time.time() < end_time:
        try:
            logger.info("🧹 [DB MAINTENANCE] Starting autonomous cloud database cleanup...")
            pool = database.get_pool()
            if pool:
                async with pool.acquire() as conn:
                    # 1. Clean expired cooldowns
                    del_cd = await conn.execute("DELETE FROM post_cooldowns WHERE expires_at < NOW();")
                    logger.info(f"🧹 [DB MAINTENANCE] Expired cooldowns purged: {del_cd}")

                    # 2. Prune old daily prices (> 5 days)
                    del_dp = await conn.execute("""
                        DELETE FROM daily_prices
                        WHERE date < (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE - ($1 * INTERVAL '1 day')
                    """, config.DAILY_PRICE_RETENTION_DAYS)
                    logger.info(f"🧹 [DB MAINTENANCE] Old daily prices purged: {del_dp}")

                    # 3. Prune old ingested channel deals (> 14 days)
                    del_icd = await conn.execute("DELETE FROM ingested_channel_deals WHERE created_at < NOW() - INTERVAL '14 days';")
                    logger.info(f"🧹 [DB MAINTENANCE] Old ingested channel deals purged: {del_icd}")

                    # 4. Finalize expired deal tracking
                    await database.finalize_expired_tracking()

                    # 5. VACUUM ANALYZE to reclaim disk space
                    await conn.execute("VACUUM ANALYZE daily_prices;")
                    await conn.execute("VACUUM ANALYZE post_cooldowns;")
                    await conn.execute("VACUUM ANALYZE ingested_channel_deals;")
                    logger.info("✅ [DB MAINTENANCE] Autonomous cloud database compaction complete!")
        except Exception as e:
            logger.warning(f"[DB MAINTENANCE] Notice: {e}")

        # Run maintenance every 4 hours (14400s)
        await asyncio.sleep(14400)

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

    # Start Autonomous Cloud Database Maintenance loop (perpetual free-tier protection)
    maintenance_task = asyncio.create_task(cloud_maintenance_loop(duration_secs))

    # Wait until duration is almost reached (reserve 2 mins for clean relay)
    sleep_time = max(10, duration_secs - 120)
    logger.info(f"⏳ Main worker active. Self-relay scheduled in {sleep_time/60:.1f} minutes.")
    try:
        await asyncio.sleep(sleep_time)
    except asyncio.CancelledError:
        logger.info("Worker run received cancellation signal.")

    # Clean shutdown before next worker spins up to guarantee ZERO MTProto session IP collision
    logger.info("Cleaning up active worker tasks and disconnecting Telethon...")
    for t in [broadcaster_task, hunter_task, keepalive_task, scraper_task, maintenance_task]:
        t.cancel()

    try:
        from budgetby.cloud_scraper import close_cloud_telethon_client
        await close_cloud_telethon_client()
    except Exception as e:
        logger.debug(f"Telethon cleanup note: {e}")

    try:
        await database.close_pool()
    except Exception as e:
        logger.debug(f"Database cleanup note: {e}")

    # Trigger relay for perpetual execution
    await self_relay_trigger()
    logger.info("Worker loop finished gracefully. Handed off to next worker.")

if __name__ == "__main__":
    asyncio.run(main())
