"""
Cleanup tasks for the scheduler.
"""
import logging
import asyncio
import os
import gzip
import shutil
import datetime
from budgetby import database, config

logger = logging.getLogger("budgetby.scheduler.cleanup")

async def daily_cleanup():
    """Orchestrates all daily maintenance."""
    try:
        logger.info("Starting daily cleanup...")
        # 1. Price and MRP Sanity Guard
        await database.execute("""
            UPDATE products SET mrp = current_price WHERE current_price > mrp AND mrp > 0;
            UPDATE products SET mrp = current_price WHERE (mrp > (15.0 * current_price) OR mrp > 500000) AND current_price > 0;
            UPDATE daily_prices dp SET close_price = p.current_price, min_price = p.current_price
            FROM products p WHERE dp.product_id = p.id AND p.mrp > 0 AND (dp.close_price > (p.mrp * 1.10) OR dp.min_price > (p.mrp * 1.10));
            UPDATE daily_prices dp SET close_price = p.current_price, min_price = p.current_price
            FROM products p WHERE dp.product_id = p.id AND p.current_price > 0 AND (dp.close_price > (p.current_price * 5.0) OR dp.close_price < (p.current_price * 0.2));
        """)

        # 2. Compress raw rows older than 5 days into 3-day buckets (must run BEFORE cleanup)
        await database.compress_old_daily_prices()

        # 3. Delete rows older than config.DAILY_PRICE_RETENTION_DAYS (benchmarks are persisted inside cleanup before delete)
        await database.cleanup_old_daily_prices()
        await database.sync_daily_price_baselines()
        await database.finalize_expired_tracking()
        
        # Prune promotional deals older than 30 days (prevents unbounded deals table growth)
        try:
            deleted_deals = await database.execute("""
                DELETE FROM deals
                WHERE posted_at < NOW() - INTERVAL '30 days';
            """)
            logger.info(f"Pruned old deals: {deleted_deals}")
        except Exception as e:
            logger.warning(f"Could not prune old deals: {e}")
        
        # Prune ingested_channel_deals older than 14 days (prevents unbounded table growth)
        try:
            deleted_icd = await database.execute("""
                DELETE FROM ingested_channel_deals
                WHERE created_at < NOW() - INTERVAL '14 days';
            """)
            logger.info(f"Pruned old ingested_channel_deals: {deleted_icd}")
        except Exception as e:
            logger.warning(f"Could not prune ingested_channel_deals: {e}")
        
        try:
            from budgetby.engine.cooldown import cleanup_expired_cooldowns
            await cleanup_expired_cooldowns()
        except Exception as e:
            logger.warning(f"Could not cleanup expired cooldowns: {e}")

        # 4. Prune local SQLite MRP history older than 14 days (Zero Cloud Egress)
        try:
            from budgetby import local_db
            await local_db.cleanup_old_mrp_history(retention_days=14)
        except Exception as e:
            logger.warning(f"Could not cleanup local MRP history: {e}")
            
        # Archive OOS > DORMANT_THRESHOLD_DAYS
        await database.execute(f"UPDATE products SET status = '{config.STATUS_DORMANT}' WHERE LOWER(status) = LOWER('{config.STATUS_TEMP_OOS}') AND last_checked < NOW() - INTERVAL '{config.DORMANT_THRESHOLD_DAYS} days'")
        
        # Delete OOS > DELETE_THRESHOLD_DAYS
        await database.execute(f"DELETE FROM products WHERE LOWER(status) IN (LOWER('{config.STATUS_TEMP_OOS}'), LOWER('{config.STATUS_DORMANT}')) AND last_checked < NOW() - INTERVAL '{config.DELETE_THRESHOLD_DAYS} days'")

        # Periodic VACUUM ANALYZE to reclaim dead tuple disk space without locks
        try:
            for tbl in ['daily_prices', 'deals', 'ingested_channel_deals', 'post_cooldowns']:
                await database.execute(f"VACUUM ANALYZE {tbl};")
        except Exception as e:
            logger.debug(f"Daily maintenance VACUUM notice: {e}")
        
                # Decay stale Tier 1 products back to Tier 3 if no price change for 7 days
        decayed = await database.execute("""
            UPDATE products 
            SET priority_tier = 3 
            WHERE priority_tier = 1 
              AND (last_price_change < NOW() - INTERVAL '7 days' OR last_price_change IS NULL)
              AND LOWER(status) = 'active';
        """)
        logger.info(f"Decayed stale Tier 1 priority products to Tier 3: {decayed}")

        logger.info("Daily cleanup completed.")
    except Exception as e:
        logger.error(f"Error during daily cleanup: {e}", exc_info=True)

async def run_backup():
    """Runs pg_dump and compresses output with native Python gzip, keeping 14-day retention."""
    if not getattr(config, "BACKUP_ENABLED", False):
        logger.info("Application-level pg_dump backup is disabled (relying on Supabase cloud auto-backup). Skipping execution.")
        return
    try:
        logger.info("Starting scheduled database backup...")
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        backup_dir = os.path.join(base_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        sql_file = os.path.join(backup_dir, f"backup_{timestamp}.sql")
        gz_file = os.path.join(backup_dir, f"backup_{timestamp}.sql.gz")

        pg_dump_bin = r"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe"
        if not os.path.exists(pg_dump_bin):
            pg_dump_bin = shutil.which("pg_dump") or "pg_dump"

        if pg_dump_bin == "pg_dump" and not shutil.which("pg_dump") and not os.path.exists(r"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe"):
            logger.warning("pg_dump executable not found on host system — skipping manual backup execution (Supabase cloud auto-backup is active).")
            return

        # Run pg_dump
        env = os.environ.copy()
        env["PGPASSWORD"] = str(config.DB_PASSWORD)

        cmd = [
            pg_dump_bin,
            "-h", str(config.DB_HOST),
            "-p", str(config.DB_PORT),
            "-U", str(config.DB_USER),
            "-d", str(config.DB_NAME),
            "-f", sql_file
        ]

        proc = await asyncio.create_subprocess_exec(*cmd, env=env)
        await proc.communicate()

        if os.path.exists(sql_file) and os.path.getsize(sql_file) > 0:
            # Compress with gzip
            with open(sql_file, "rb") as f_in:
                with gzip.open(gz_file, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            os.remove(sql_file)

            size_mb = os.path.getsize(gz_file) / (1024 * 1024)
            logger.info(f"✅ Database backup created successfully: {gz_file} ({size_mb:.2f} MB)")

            # Clean backups older than 14 days
            now = datetime.datetime.now()
            for f in os.listdir(backup_dir):
                if f.endswith(".sql.gz"):
                    fp = os.path.join(backup_dir, f)
                    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fp))
                    if (now - mtime).days > 14:
                        os.remove(fp)
                        logger.info(f"Pruned old backup: {f}")
        else:
            logger.warning("Backup file was empty or failed to generate.")

    except Exception as e:
        logger.error(f"Error during backup: {e}", exc_info=True)

async def monthly_maintenance():
    """Monthly benchmark shift."""
    try:
        logger.info("Starting monthly maintenance...")
        await database.monthly_benchmark_shift()
        logger.info("Monthly maintenance completed.")
    except Exception as e:
        logger.error(f"Error during monthly maintenance: {e}", exc_info=True)

async def catchup_scan():
    """Catchup scan for missed high-priority products & automated title sanitization."""
    try:
        logger.info("Starting catchup scan and title sanitization...")
        await database.execute("UPDATE products SET next_check = NOW() WHERE priority_tier = 1 OR current_price IS NULL")
        
        # Autonomous title sanitizer for all platforms
        rows = await database.fetch("""
            SELECT id, product_url, platform 
            FROM products 
            WHERE title = 'Product' OR title = 'Flipkart Product' OR length(title) < 5
            LIMIT 500;
        """)
        import re
        for r in rows:
            p_url = r["product_url"] or ""
            slug = re.sub(r'https?://[^/]+/', '', p_url).split('/p/')[0].split('?')[0].lstrip('/').replace('-', ' ').title()
            if len(slug) >= 4:
                await database.execute("UPDATE products SET title = $2 WHERE id = $1", r["id"], slug)
        
        logger.info("Catchup scan completed.")
    except Exception as e:
        logger.error(f"Error during catchup scan: {e}")
