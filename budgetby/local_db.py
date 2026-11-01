import aiosqlite
import asyncio
import logging
from typing import List, Dict, Any, Optional
import datetime
from budgetby import config

logger = logging.getLogger(__name__)

import pathlib
DB_PATH = pathlib.Path(__file__).parent.parent / "budgetby_local.db"

_write_queue = None
_writer_task = None
_db_conn = None
_in_flight_products = set()

def mark_in_flight(platform: str, platform_id: str) -> bool:
    """Marks a product as currently being scraped. Returns True if acquired, False if already in-flight."""
    global _in_flight_products
    if not platform or not platform_id:
        return False
    key = (platform.lower().strip(), str(platform_id).strip())
    if key in _in_flight_products:
        return False
    _in_flight_products.add(key)
    return True

def release_in_flight(platform: str, platform_id: str):
    """Releases in-flight state when scraping completes or errors."""
    global _in_flight_products
    if not platform or not platform_id:
        return
    key = (platform.lower().strip(), str(platform_id).strip())
    _in_flight_products.discard(key)

def is_in_flight(platform: str, platform_id: str) -> bool:
    """Checks if a product is currently in-flight."""
    if not platform or not platform_id:
        return False
    key = (platform.lower().strip(), str(platform_id).strip())
    return key in _in_flight_products

def get_in_flight_count() -> int:
    """Returns number of products currently in-flight."""
    return len(_in_flight_products)

async def init_db():
    global _db_conn, _writer_task, _write_queue, _mrp_observation_cache
    
    _mrp_observation_cache = {}
    _write_queue = asyncio.Queue()
    _db_conn = await aiosqlite.connect(DB_PATH)
    _db_conn.row_factory = aiosqlite.Row
    
    # Enable WAL mode for high concurrency
    await _db_conn.execute("PRAGMA journal_mode=WAL;")
    await _db_conn.execute("PRAGMA synchronous=NORMAL;")
    
    # Create products table (Mirror)
    await _db_conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            platform TEXT,
            platform_id TEXT,
            url TEXT,
            current_price REAL,
            mrp REAL,
            priority_tier INTEGER DEFAULT 3,
            next_check TIMESTAMP,
            status TEXT DEFAULT 'ACTIVE',
            PRIMARY KEY (platform, platform_id)
        );
    """)
    
    # Create indexes for fast scanning
    await _db_conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_next_check ON products(priority_tier ASC, next_check ASC);
    """)
    
    # Create pending_syncs table (Outbox Queue)
    await _db_conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_syncs (
            platform TEXT,
            platform_id TEXT,
            new_price REAL,
            in_stock BOOLEAN,
            status TEXT,
            PRIMARY KEY (platform, platform_id)
        );
    """)
    
    # Create mrp_history table for 14-day fake-discount validation (Zero Supabase Egress)
    await _db_conn.execute("""
        CREATE TABLE IF NOT EXISTS mrp_history (
            platform TEXT,
            platform_id TEXT,
            date TEXT,
            mrp REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (platform, platform_id, date)
        );
    """)
    await _db_conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_mrp_hist ON mrp_history(platform, platform_id, date DESC);
    """)
    
    await _db_conn.commit()
    
    # Start the single writer task
    _writer_task = asyncio.create_task(_writer_loop())
    logger.info("Local SQLite database initialized.")

async def close_db():
    global _writer_task, _db_conn, _mrp_observation_cache, _in_flight_products
    if _writer_task:
        _writer_task.cancel()
    if _db_conn:
        await _db_conn.close()
    _mrp_observation_cache.clear()
    _in_flight_products.clear()

async def _writer_loop():
    """Single background worker that executes writes sequentially to prevent DB locking."""
    global _db_conn
    while True:
        try:
            # Get next query and parameters
            query, params = await _write_queue.get()
            try:
                await _db_conn.execute(query, params)
                await _db_conn.commit()
            except Exception as e:
                logger.error(f"Local DB Write Error: {e} | Query: {query} | Params: {params}")
            finally:
                _write_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Local DB Writer loop error: {e}")
            await asyncio.sleep(1)

def queue_write(query: str, params: tuple):
    """Enqueue a write operation to be executed by the single writer loop."""
    global _write_queue
    if _write_queue is None:
        try:
            _write_queue = asyncio.Queue()
        except Exception:
            return
    _write_queue.put_nowait((query, params))

async def get_products_due_for_check(limit: int = 40, exclude_in_flight: bool = True) -> List[aiosqlite.Row]:
    """Get the next batch of products due for checking."""
    if not _db_conn:
        return []
        
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    # Fetch extra candidates if some might be in-flight
    fetch_limit = limit * 2 if exclude_in_flight and _in_flight_products else limit
    
    # We order by priority_tier ASC first (to catch up on Hot Deals), then next_check
    query = """
        SELECT platform, platform_id, url, current_price, mrp, priority_tier, next_check, status
        FROM products
        WHERE next_check <= ? AND status != 'DORMANT' AND status != 'ARCHIVED'
        ORDER BY priority_tier ASC, next_check ASC
        LIMIT ?
    """
    
    async with _db_conn.execute(query, (now, fetch_limit)) as cursor:
        rows = await cursor.fetchall()
        if not exclude_in_flight or not _in_flight_products:
            return rows[:limit]
        
        filtered = [
            r for r in rows
            if (r["platform"].lower().strip(), str(r["platform_id"]).strip()) not in _in_flight_products
        ]
        return filtered[:limit]

def update_product_check_time(platform: str, platform_id: str, priority_tier: int):
    """Update the local next_check time based on priority tier."""
    interval = config.PRIORITY_INTERVALS.get(priority_tier, 48 * 3600)
    next_check = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=interval)).isoformat()
    
    query = """
        UPDATE products 
        SET priority_tier = ?, next_check = ?
        WHERE platform = ? AND platform_id = ?
    """
    queue_write(query, (priority_tier, next_check, platform, platform_id))

def queue_pending_sync(platform: str, platform_id: str, new_price: float, in_stock: bool, status: str):
    """Add a cold-path price update to the pending_syncs outbox queue. UPSERT prevents zombies."""
    # First, update the local products mirror so it has the new price instantly
    queue_write("""
        UPDATE products 
        SET current_price = ?, status = ?
        WHERE platform = ? AND platform_id = ?
    """, (new_price, status, platform, platform_id))
    
    # Next, add to the sync queue for the 10 PM batch. Upsert logic guarantees only the newest price is sent.
    query = """
        INSERT INTO pending_syncs (platform, platform_id, new_price, in_stock, status)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(platform, platform_id) DO UPDATE SET
            new_price = excluded.new_price,
            in_stock = excluded.in_stock,
            status = excluded.status
    """
    queue_write(query, (platform, platform_id, float(new_price), in_stock, status))

def update_product_locally(platform: str, platform_id: str, url: str, current_price: float, mrp: float, priority_tier: int, status: str):
    """Insert or update a product in the local SQLite mirror (e.g., from the bootstrap or when a new product is found)."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    query = """
        INSERT INTO products (platform, platform_id, url, current_price, mrp, priority_tier, next_check, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, platform_id) DO UPDATE SET
            url = excluded.url,
            current_price = excluded.current_price,
            mrp = excluded.mrp,
            priority_tier = excluded.priority_tier,
            status = excluded.status
    """
    queue_write(query, (platform, platform_id, url, float(current_price), float(mrp), priority_tier, now, status))
    if mrp and float(mrp) > 0:
        record_mrp_observation(platform, platform_id, float(mrp))

# In-memory cache for same-day MRP observations to eliminate redundant SQLite writes
# Key: (platform, platform_id, date_str) -> mrp_val
_mrp_observation_cache: Dict[tuple, float] = {}

def record_mrp_observation(platform: str, platform_id: str, mrp: float, date_str: str = None) -> bool:
    """
    Records a daily MRP observation in local SQLite for 14-day fake-discount validation.
    Eliminates write amplification: only writes on the FIRST daily observation or when MRP changes.
    Zero Supabase reads/writes.
    
    Returns:
        bool: True if an SQLite write was queued, False if skipped as a same-day duplicate.
    """
    if not mrp or not platform or not platform_id:
        return False
    try:
        mrp_val = round(float(mrp), 2)
        if mrp_val <= 0:
            return False
    except (ValueError, TypeError):
        return False

    p_key = platform.lower().strip()
    pid_key = str(platform_id).strip()
    date_val = date_str or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    cache_key = (p_key, pid_key, date_val)

    # If same product, same date, and same MRP, skip redundant SQLite write
    cached_mrp = _mrp_observation_cache.get(cache_key)
    if cached_mrp is not None and abs(cached_mrp - mrp_val) < 0.01:
        return False

    # Update memory cache and queue single SQLite UPSERT
    _mrp_observation_cache[cache_key] = mrp_val
    query = """
        INSERT INTO mrp_history (platform, platform_id, date, mrp)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(platform, platform_id, date) DO UPDATE SET
            mrp = excluded.mrp
    """
    queue_write(query, (p_key, pid_key, date_val, mrp_val))
    return True

async def get_mrp_history(platform: str, platform_id: str, days: int = 14) -> List[Dict[str, Any]]:
    """
    Fetches the last N days of MRP observations from local SQLite for fake-discount analysis.
    Zero Supabase egress.
    """
    if not _db_conn or not platform or not platform_id:
        return []
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    query = """
        SELECT date, mrp
        FROM mrp_history
        WHERE platform = ? AND platform_id = ? AND date >= ?
        ORDER BY date ASC
    """
    async with _db_conn.execute(query, (platform.lower().strip(), str(platform_id).strip(), cutoff)) as cursor:
        rows = await cursor.fetchall()
        return [{"date": r["date"], "mrp": float(r["mrp"])} for r in rows]

async def cleanup_old_mrp_history(retention_days: int = 14) -> int:
    """
    Prunes local SQLite MRP history older than retention_days (default 14 days).
    Also prunes the in-memory MRP observation cache.
    Runs as part of daily local maintenance.
    """
    global _mrp_observation_cache
    if not _db_conn:
        return 0
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=retention_days)).strftime("%Y-%m-%d")
    async with _db_conn.execute("DELETE FROM mrp_history WHERE date < ?", (cutoff,)) as cursor:
        deleted = cursor.rowcount
    await _db_conn.commit()

    # Prune in-memory cache
    _mrp_observation_cache = {
        k: v for k, v in _mrp_observation_cache.items()
        if len(k) >= 3 and k[2] >= cutoff
    }
    logger.info(f"Pruned {deleted} old local MRP history records (>{retention_days} days).")
    return deleted

async def get_and_clear_pending_syncs() -> List[aiosqlite.Row]:
    """Used by the 10 PM batch job to fetch all queued updates and empty the table."""
    if not _db_conn:
        return []
        
    # Wait for the write queue to drain so we don't miss anything pending in memory
    await _write_queue.join()
    
    async with _db_conn.execute("SELECT * FROM pending_syncs") as cursor:
        rows = await cursor.fetchall()
        
    # Clear the queue
    if rows:
        await _db_conn.execute("DELETE FROM pending_syncs")
        await _db_conn.commit()
        
    return rows

async def vacuum_db():
    """Defragment the SQLite database to save disk space."""
    if _db_conn:
        await _write_queue.join() # Wait for writes to finish
        logger.info("Vacuuming Local SQLite database...")
        await _db_conn.execute("VACUUM;")
        await _db_conn.commit()
        logger.info("Vacuum complete.")
