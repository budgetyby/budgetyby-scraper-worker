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
import signal
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
        return False

    logger.info(f"🔄 [AUTO-RELAY] Dispatching next GitHub Actions workflow run for {repo}...")
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {gh_token}",
        "User-Agent": "BudgetBy-Worker"
    }
    url = f"https://api.github.com/repos/{repo}/actions/workflows/cloud_scraper.yml/dispatches"
    for attempt in range(3):
        try:
            async with AsyncSession(timeout=15) as session:
                resp = await session.post(url, headers=headers, json={"ref": "main"})
                if resp.status_code in (201, 204):
                    logger.info("🎉 [AUTO-RELAY] Next 24/7 worker workflow dispatched successfully!")
                    return True
                else:
                    logger.warning(f"[AUTO-RELAY] Dispatch attempt {attempt+1} returned {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"[AUTO-RELAY] Attempt {attempt+1} error dispatching next run: {e}")
        await asyncio.sleep(10)
    return False

async def broadcaster_stall_watchdog():
    """Monitors broadcaster activity. If no deal is posted for 15+ minutes, logs alert."""
    from budgetby.broadcaster import broadcaster_stats, get_broadcaster_stats
    last_count = -1
    stall_minutes = 0
    while True:
        await asyncio.sleep(60)
        curr = broadcaster_stats.get("total_posted", 0)
        if curr == last_count:
            stall_minutes += 1
            if stall_minutes in (15, 20, 30):
                logger.warning(f"⚠️ [BROADCASTER WATCHDOG] No deals posted for {stall_minutes} minutes! Status: {get_broadcaster_stats()}")
        else:
            last_count = curr
            stall_minutes = 0

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

    # Distributed Lease Check on startup:
    # If another runner is already actively heartbeating, exit immediately to save runner minutes and prevent IP collision
    is_github_actions = bool(os.getenv("GITHUB_ACTIONS"))
    runner_id = os.getenv("GITHUB_RUN_ID") or f"runner_{os.getpid()}_{int(time.time())}"
    lease = await database.get_telethon_lease()
    if lease and lease.get("runner_id") and lease.get("runner_id") != runner_id:
        last_hb = lease.get("last_heartbeat")
        if last_hb:
            elapsed = (datetime.now(timezone.utc) - last_hb).total_seconds()
            if elapsed < 90:
                logger.info(f"🔒 [RUNNER GUARD] Runner #{lease.get('runner_id')} is actively healthy (heartbeat {elapsed:.0f}s ago). Exiting watchdog runner gracefully.")
                await database.close_pool()
                return

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

    # Start Continuous Affiliate Shortlink Converter Worker (1-by-1 safe conversion of raw deals)
    from budgetby.affiliate.converter_worker import run_affiliate_converter_worker
    converter_task = asyncio.create_task(run_affiliate_converter_worker())

    # Start Broadcaster Stall Watchdog
    watchdog_task = asyncio.create_task(broadcaster_stall_watchdog())

    # Register signal handlers for clean cloud cancellation (SIGTERM/SIGINT)
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    def _sig_handler():
        logger.info("🛑 [SIGNAL] Intercepted termination signal. Initiating graceful shutdown and Telethon release...")
        shutdown_event.set()

    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _sig_handler)
            except Exception:
                pass

    # Wait until duration is almost reached (reserve 5 mins for clean relay)
    sleep_time = max(10, duration_secs - 300)
    logger.info(f"⏳ Main worker active. Self-relay scheduled in {sleep_time/60:.1f} minutes.")
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
        logger.info("Shutdown event triggered before relay timer.")
    except asyncio.TimeoutError:
        logger.info(f"Worker run duration limit ({args.duration_minutes}m) reached. Initiating clean handoff...")

    # Clean shutdown before next worker spins up to guarantee ZERO MTProto session IP collision
    logger.info("Cleaning up active worker tasks and disconnecting Telethon...")
    for t in [broadcaster_task, hunter_task, keepalive_task, scraper_task, maintenance_task, converter_task, watchdog_task]:
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

    # Trigger relay for perpetual execution only if not cancelled by external SIGTERM
    if not shutdown_event.is_set():
        relay_ok = await self_relay_trigger()
        if relay_ok:
            logger.info("Worker loop finished gracefully. Handed off to next worker.")
        else:
            logger.warning("Relay trigger did not confirm; scheduled cron watchdog will resume shortly.")
    else:
        logger.info("Worker stopped cleanly on external termination signal.")

if __name__ == "__main__":
    asyncio.run(main())
