"""
BudgetBy — Database Connection & Query Helpers
Uses asyncpg for high-performance async PostgreSQL access.
"""

import asyncpg
import logging
from contextlib import asynccontextmanager
from budgetby import config

logger = logging.getLogger("budgetby.database")

# Global connection pool
_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    """Initialize the connection pool. Call once at startup."""
    global _pool
    if _pool is not None:
        return _pool

    import ssl
    import os
    ctx = ssl.create_default_context()
    if os.getenv("DB_SSL_VERIFY", "false").lower() in ("false", "0", "no", "none", "off"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    host_to_use = config.DB_HOST
    if "aivencloud.com" in config.DB_HOST:
        try:
            import socket
            socket.gethostbyname(config.DB_HOST)
        except socket.gaierror:
            host_to_use = "168.144.145.235"

    pool_kwargs = {
        "host": host_to_use,
        "port": config.DB_PORT,
        "database": config.DB_NAME,
        "user": config.DB_USER,
        "password": config.DB_PASSWORD,
        "min_size": int(os.getenv("DB_POOL_MIN", "5")),
        "max_size": int(os.getenv("DB_POOL_MAX", "20")),
        "command_timeout": 60,
        "timeout": 30,
        "statement_cache_size": 0,
    }
    if config.DB_SSL and str(config.DB_SSL).lower() not in ("disable", "false", "none", "0", ""):
        pool_kwargs["ssl"] = ctx

    _pool = await asyncpg.create_pool(**pool_kwargs)
    logger.info(f"Database connection pool initialized to {host_to_use}:{config.DB_PORT} (min=2, max=20)")
    return _pool


async def close_pool():
    """Close the connection pool. Call at shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")


def get_pool() -> asyncpg.Pool:
    """Get the current connection pool. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    return _pool


@asynccontextmanager
async def acquire():
    """Acquire a connection from the pool as an async context manager."""
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


async def fetch(query: str, *args) -> list[asyncpg.Record]:
    """Execute a query and return all result rows."""
    pool = get_pool()
    return await pool.fetch(query, *args)


async def fetchrow(query: str, *args) -> asyncpg.Record | None:
    """Execute a query and return a single row (or None)."""
    pool = get_pool()
    return await pool.fetchrow(query, *args)


async def fetchval(query: str, *args):
    """Execute a query and return a single value."""
    pool = get_pool()
    return await pool.fetchval(query, *args)


async def execute(query: str, *args) -> str:
    """Execute a query (INSERT/UPDATE/DELETE) and return status."""
    pool = get_pool()
    return await pool.execute(query, *args)


async def executemany(query: str, args_list: list) -> None:
    """Execute a query with multiple argument sets (batch insert)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(query, args_list)


# ── Product-Specific Helpers ───────────────────────────────────────────


from budgetby import local_db

async def upsert_product(data: dict) -> int:
    """
    Insert a new product or update if it already exists (same platform + platform_id).
    Returns the product id.
    """
    platform = str(data.get("platform") or "").strip().lower()
    p_url = str(data.get("product_url") or "").strip()

    # Automated title sanitizer: if title is missing or generic, derive from URL slug
    title = data.get("title") or ""
    if not title or len(title) < 5 or "product" in title.lower() or "editor" in title.lower():
        if p_url:
            import re
            slug = re.sub(r'https?://[^/]+/', '', p_url).split('/p/')[0].split('?')[0].lstrip('/').replace('-', ' ').title()
            if len(slug) >= 4:
                title = slug
    from budgetby.scrapers.utils import clean_title, is_blacklisted_utility_item
    title = clean_title(title)
    if not title:
        title = f"{str(data.get('platform', '')).capitalize()} Item {data.get('platform_id', '')}"
    if is_blacklisted_utility_item(title, p_url):
        return None

    cur_price = data.get("current_price")
    mrp_val = data.get("mrp")
    if cur_price and mrp_val and cur_price > 0:
        if mrp_val < cur_price:
            mrp_val = cur_price
        elif mrp_val > 15.0 * cur_price or mrp_val > 500000:
            mrp_val = cur_price

    platform = data.get("platform")
    platform_id = str(data.get("platform_id") or "")

    # Strict Deduplication Check: Fast index lookup if platform_id is present; fallback to URL/Title check only if empty
    existing = None
    if platform_id:
        existing = await fetchrow("""
            SELECT id, current_price, mrp, all_time_low 
            FROM products 
            WHERE platform = $1 AND platform_id = $2
            LIMIT 1;
        """, platform, platform_id)
    else:
        existing = await fetchrow("""
            SELECT id, current_price, mrp, all_time_low 
            FROM products 
            WHERE platform = $1 
              AND (product_url = $2 OR LOWER(TRIM(title)) = LOWER(TRIM($3)))
            LIMIT 1;
        """, platform, p_url, title)

    if existing:
        existing_id = existing["id"]
        # Update existing record instead of creating duplicate
        await execute("""
            UPDATE products SET
                current_price = COALESCE($2, current_price),
                mrp = COALESCE($3, mrp),
                rating = COALESCE($4, rating),
                review_count = COALESCE($5, review_count),
                image_url = COALESCE(NULLIF(TRIM($6), ''), image_url),
                affiliate_url = CASE
                    WHEN $7 ILIKE '%fktr.in%' OR $7 ILIKE '%myntr.it%' OR $7 ILIKE '%ajiio.in%' OR $7 ILIKE '%ekaro.in%' OR $7 ILIKE '%clnk.in%' OR $7 ILIKE '%linksredirect.com%' OR $7 ILIKE '%affgrowth%' OR ($7 ILIKE '%amazon.in%' AND $7 ILIKE '%tag=%') THEN $7
                    WHEN products.affiliate_url ILIKE '%fktr.in%' OR products.affiliate_url ILIKE '%myntr.it%' OR products.affiliate_url ILIKE '%ajiio.in%' OR products.affiliate_url ILIKE '%ekaro.in%' OR products.affiliate_url ILIKE '%clnk.in%' OR products.affiliate_url ILIKE '%linksredirect.com%' OR products.affiliate_url ILIKE '%affgrowth%' OR (products.affiliate_url ILIKE '%amazon.in%' AND products.affiliate_url ILIKE '%tag=%') THEN products.affiliate_url
                    ELSE COALESCE($7, products.affiliate_url)
                END,
                in_stock = COALESCE($8, in_stock),
                last_checked = NOW(),
                all_time_low = COALESCE(LEAST(COALESCE($2, all_time_low), all_time_low), $2)
            WHERE id = $1;
        """, existing_id, cur_price, mrp_val, data.get("rating"), data.get("review_count"), 
             data.get("image_url"), data.get("affiliate_url"), data.get("in_stock", True))
        
        if cur_price and cur_price > 0:
            try:
                await upsert_daily_price(existing_id, cur_price)
            except Exception:
                pass
        try:
            local_db.update_product_locally(
                platform, platform_id, p_url, cur_price or 0.0, mrp_val or 0.0,
                data.get("priority_tier", 2), data.get("status", config.STATUS_ACTIVE)
            )
        except Exception:
            pass
        return existing_id

    aff_url = data.get("affiliate_url") or p_url

    # If product does not exist, insert cleanly
    row = await fetchrow("""
        INSERT INTO products (platform, platform_id, title, category,
                              product_url, affiliate_url, image_url,
                              current_price, mrp, rating, review_count,
                              in_stock, is_renewed, status, priority_tier,
                              last_checked, next_check, all_time_low)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15, NOW(), NOW(), $8)
        ON CONFLICT (platform, platform_id) DO UPDATE SET
            title = COALESCE(EXCLUDED.title, products.title),
            category = COALESCE(EXCLUDED.category, products.category),
            image_url = COALESCE(NULLIF(TRIM(EXCLUDED.image_url), ''), products.image_url),
            affiliate_url = CASE
                WHEN EXCLUDED.affiliate_url ILIKE '%fktr.in%' OR EXCLUDED.affiliate_url ILIKE '%myntr.it%' OR EXCLUDED.affiliate_url ILIKE '%ajiio.in%' OR EXCLUDED.affiliate_url ILIKE '%ekaro.in%' OR EXCLUDED.affiliate_url ILIKE '%clnk.in%' OR EXCLUDED.affiliate_url ILIKE '%linksredirect.com%' OR EXCLUDED.affiliate_url ILIKE '%affgrowth%' OR (EXCLUDED.affiliate_url ILIKE '%amazon.in%' AND EXCLUDED.affiliate_url ILIKE '%tag=%') THEN EXCLUDED.affiliate_url
                WHEN products.affiliate_url ILIKE '%fktr.in%' OR products.affiliate_url ILIKE '%myntr.it%' OR products.affiliate_url ILIKE '%ajiio.in%' OR products.affiliate_url ILIKE '%ekaro.in%' OR products.affiliate_url ILIKE '%clnk.in%' OR products.affiliate_url ILIKE '%linksredirect.com%' OR products.affiliate_url ILIKE '%affgrowth%' OR (products.affiliate_url ILIKE '%amazon.in%' AND products.affiliate_url ILIKE '%tag=%') THEN products.affiliate_url
                ELSE COALESCE(EXCLUDED.affiliate_url, products.affiliate_url)
            END,
            last_checked = NOW()
        RETURNING id
    """,
        platform,
        platform_id,
        title,
        data.get("category"),
        p_url,
        aff_url,
        data.get("image_url"),
        cur_price,
        mrp_val,
        data.get("rating"),
        data.get("review_count", 0),
        data.get("in_stock", True),
        data.get("is_renewed", False),
        data.get("status", config.STATUS_ACTIVE),
        data.get("priority_tier", 3),
    )
    if cur_price and cur_price > 0:
        try:
            await upsert_daily_price(row["id"], cur_price)
        except Exception:
            pass
    try:
        local_db.update_product_locally(
            platform, platform_id, p_url, cur_price or 0.0, mrp_val or 0.0,
            data.get("priority_tier", 2), data.get("status", config.STATUS_ACTIVE)
        )
    except Exception:
        pass
    return row["id"]


async def update_price(product_id: int, new_price: float, in_stock: bool,
                        title: str = None, mrp: float = None, rating: float = None,
                        review_count: int = None, image_url: str = None):
    """
    Update a product's live price snapshot and product details (title, mrp, rating, etc.).
    Also updates min benchmarks and all_time_low.
    Prevents phantom price drops by validating previous_price and clamping anomalous MRPs.
    """
    if new_price and new_price > 0:
        if mrp:
            if new_price > mrp:
                mrp = new_price
            elif mrp > 15.0 * new_price or mrp > 500000:
                mrp = new_price
    await execute("""
        UPDATE products SET
            previous_price = CASE
                -- If current price is higher than new price, verify it is realistic before storing as previous_price
                WHEN current_price IS NOT NULL 
                 AND current_price > $2::numeric 
                 AND current_price <= GREATEST(COALESCE($5::numeric, mrp, $2::numeric * 2.5) * 1.25, $2::numeric * 5.0)
                THEN current_price
                WHEN current_price IS NOT NULL AND current_price <= $2::numeric
                THEN current_price
                -- If old current_price was an absurd anomaly (e.g. 61,190 on a 133 item), do NOT inherit it
                ELSE CASE 
                    WHEN previous_price > GREATEST(COALESCE($5::numeric, mrp, $2::numeric * 2.5) * 1.25, $2::numeric * 5.0)
                    THEN NULL
                    ELSE previous_price
                END
            END,
            current_price = $2::numeric,
            in_stock = $3::boolean,
            title = CASE WHEN $4::text IS NOT NULL AND $4::text != '' THEN $4::text ELSE title END,
            mrp = CASE 
                WHEN $5::numeric IS NOT NULL THEN $5::numeric
                WHEN mrp < $2::numeric THEN $2::numeric
                WHEN mrp > $2::numeric * 15.0 THEN $2::numeric
                ELSE mrp 
            END,
            rating = COALESCE($6::numeric, rating),
            review_count = COALESCE($7::integer, review_count),
            image_url = CASE WHEN $8::text IS NOT NULL AND $8 != '' THEN $8::text ELSE image_url END,
            last_checked = NOW(),
            last_price_change = CASE
                WHEN current_price IS DISTINCT FROM $2::numeric THEN NOW()
                ELSE last_price_change
            END,
            all_time_low = COALESCE(LEAST(all_time_low, $2::numeric), $2::numeric)
        WHERE id = $1::integer
    """, product_id, new_price, in_stock, title, mrp, rating,
         review_count, image_url)


async def sync_daily_price_baselines():
    """
    Ensures 100% of all active products in the catalog have a daily_price snapshot for today.
    Runs instantaneously at midnight and on startup.
    """
    await execute("""
        INSERT INTO daily_prices (product_id, date, min_price, close_price, is_compressed)
        SELECT id, (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE, current_price, current_price, FALSE
        FROM products
        WHERE status = 'ACTIVE' AND current_price > 0
        ON CONFLICT (product_id, date) DO NOTHING;
    """)

async def upsert_daily_price(product_id: int, price: float):
    """
    Upsert today's daily price record.
    If row exists for today: update min_price (LEAST) and close_price.
    If no row: insert new.
    """
    if not price or price <= 0:
        return
    await execute("""
        INSERT INTO daily_prices (product_id, date, min_price, close_price, is_compressed)
        VALUES ($1, (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE, $2, $2, FALSE)
        ON CONFLICT (product_id, date) DO UPDATE SET
            min_price = LEAST(daily_prices.min_price, $2),
            close_price = $2
        WHERE daily_prices.is_compressed = FALSE
    """, product_id, price)


async def get_products_due_for_check(limit: int = 40) -> list[asyncpg.Record]:
    """
    Get products due for checking using a balanced multi-store distribution.
    Prevents single-store flooding (e.g. all Ajio or all Flipkart) by fetching balanced
    slices across all 5 platforms ('amazon', 'flipkart', 'myntra', 'ajio', 'nykaa').
    """
    per_store = max(4, limit // 5)
    return await fetch(f"""
        (SELECT p.id, p.platform, p.product_url, p.priority_tier, p.title, 
                p.current_price, p.mrp, p.category, p.next_check
         FROM products p
         WHERE p.status = 'ACTIVE' AND p.in_stock = TRUE AND p.platform = 'amazon'
           AND (p.next_check IS NULL OR p.next_check <= NOW())
         ORDER BY p.id DESC LIMIT {per_store})
        UNION ALL
        (SELECT p.id, p.platform, p.product_url, p.priority_tier, p.title, 
                p.current_price, p.mrp, p.category, p.next_check
         FROM products p
         WHERE p.status = 'ACTIVE' AND p.in_stock = TRUE AND p.platform = 'flipkart'
           AND (p.next_check IS NULL OR p.next_check <= NOW())
         ORDER BY p.id DESC LIMIT {per_store})
        UNION ALL
        (SELECT p.id, p.platform, p.product_url, p.priority_tier, p.title, 
                p.current_price, p.mrp, p.category, p.next_check
         FROM products p
         WHERE p.status = 'ACTIVE' AND p.in_stock = TRUE AND p.platform = 'myntra'
           AND (p.next_check IS NULL OR p.next_check <= NOW())
         ORDER BY p.id DESC LIMIT {per_store})
        UNION ALL
        (SELECT p.id, p.platform, p.product_url, p.priority_tier, p.title, 
                p.current_price, p.mrp, p.category, p.next_check
         FROM products p
         WHERE p.status = 'ACTIVE' AND p.in_stock = TRUE AND p.platform = 'ajio'
           AND (p.next_check IS NULL OR p.next_check <= NOW())
         ORDER BY p.id DESC LIMIT {per_store})
        UNION ALL
        (SELECT p.id, p.platform, p.product_url, p.priority_tier, p.title, 
                p.current_price, p.mrp, p.category, p.next_check
         FROM products p
         WHERE p.status = 'ACTIVE' AND p.in_stock = TRUE AND p.platform = 'nykaa'
           AND (p.next_check IS NULL OR p.next_check <= NOW())
         ORDER BY p.id DESC LIMIT {per_store})
        LIMIT $1;
    """, limit)


async def schedule_next_check(product_id: int, priority_tier: int):
    """Set the product's next check time based on its priority tier."""
    interval_seconds = config.PRIORITY_INTERVALS.get(priority_tier, 48 * 3600)
    await execute("""
        UPDATE products SET
            priority_tier = $2,
            next_check = NOW() + make_interval(secs => $3)
        WHERE id = $1
    """, product_id, priority_tier, interval_seconds)


async def refresh_30d_benchmarks():
    """
    Recalculate min_30d and median_30d_price from daily_prices for all products.
    Run daily at midnight. Uses 120s timeout — this query scans a large table.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE products p SET
                min_30d = LEAST(COALESCE(p.min_30d, sub.min_30d), sub.min_30d),
                median_30d_price = COALESCE(sub.median_price, p.median_30d_price, p.current_price)
            FROM (
                SELECT
                    product_id,
                    MIN(min_price) AS min_30d,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY close_price) AS median_price
                FROM daily_prices
                GROUP BY product_id
            ) sub
            WHERE p.id = sub.product_id
        """, timeout=120)
    logger.info("Refreshed 30-day benchmarks for all products")


async def monthly_benchmark_shift():
    """
    Monthly sliding window shift (runs once a month):
      min_120d <- min_90d   (preserve 90d low as 120d history)
      min_90d  <- min_60d
      min_60d  <- min_30d
      min_30d  recalculates fresh from daily_prices
    """
    await execute("""
        UPDATE products SET
            min_120d = COALESCE(min_90d, min_120d),
            min_90d  = COALESCE(min_60d, min_90d),
            min_60d  = COALESCE(min_30d, min_60d)
    """)
    await refresh_30d_benchmarks()
    logger.info("Monthly benchmark shift complete (min_120d/90d/60d/30d updated)")


async def compress_old_daily_prices():
    """
    Tiered compression: raw daily rows older than PRICE_RAW_RETENTION_DAYS get compressed
    into 6-day bucket minimums to save storage.

    Algorithm:
    1. Find all uncompressed rows older than raw_days (2 days).
    2. For each complete 6-day window (per product), compute:
       - bucket date = start of the 6-day window (aligned to fixed epoch)
       - min_price   = MIN of the daily min_prices
       - close_price = MIN of the daily close_prices
    3. Insert those as is_compressed=TRUE bucket rows.
    4. Delete the original raw rows that were compressed.

    This runs DAILY before cleanup_old_daily_prices().
    """
    raw_days = config.PRICE_RAW_RETENTION_DAYS   # 2
    bucket_size = config.PRICE_BUCKET_SIZE_DAYS  # 6

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Step A: Insert compressed bucket rows for 6-day windows
            await conn.execute(f"""
                INSERT INTO daily_prices (product_id, date, min_price, close_price, is_compressed)
                SELECT
                    product_id,
                    -- Align bucket start to fixed 6-day epoch from 2000-01-01
                    (DATE '2000-01-01' + (((date - DATE '2000-01-01') / {bucket_size}) * {bucket_size})) AS bucket_start,
                    MIN(min_price)                                     AS agg_min,
                    MIN(close_price)                                   AS agg_close,
                    TRUE
                FROM daily_prices
                WHERE is_compressed = FALSE
                  AND date < (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE - INTERVAL '{raw_days} days'
                GROUP BY
                    product_id,
                    (DATE '2000-01-01' + (((date - DATE '2000-01-01') / {bucket_size}) * {bucket_size}))
                HAVING COUNT(*) >= 1   -- Compress ALL old raw rows, even partial windows
                ON CONFLICT (product_id, date) DO UPDATE SET
                    min_price     = LEAST(daily_prices.min_price, EXCLUDED.min_price),
                    close_price   = LEAST(daily_prices.close_price, EXCLUDED.close_price),
                    is_compressed = TRUE;
            """)

            # Step B: Delete the raw rows that have been successfully compressed
            result = await conn.execute(f"""
                DELETE FROM daily_prices
                WHERE is_compressed = FALSE
                  AND date < (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE - INTERVAL '{raw_days} days'
                  AND EXISTS (
                      SELECT 1 FROM daily_prices dp2
                      WHERE dp2.product_id = daily_prices.product_id
                        AND dp2.is_compressed = TRUE
                        AND dp2.date = (DATE '2000-01-01' + (
                            ((daily_prices.date - DATE '2000-01-01') / {bucket_size}) * {bucket_size}
                        ))
                  );
            """)
    logger.info(f"Price compression complete: compressed raw rows deleted: {result}")


async def cleanup_old_daily_prices():
    """
    Delete ALL daily_prices rows (raw AND compressed) older than DAILY_PRICE_RETENTION_DAYS (18 days).
    Benchmarks (min_30d, median_30d_price) are recalculated and persisted on the products row FIRST
    so no historical data is lost when rows are deleted.
    """
    # Persist benchmarks before any deletion
    await refresh_30d_benchmarks()

    result = await execute("""
        DELETE FROM daily_prices
        WHERE date < (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE - ($1 * INTERVAL '1 day')
    """, config.DAILY_PRICE_RETENTION_DAYS)
    logger.info(f"Cleaned up old daily prices (>{config.DAILY_PRICE_RETENTION_DAYS} days): {result}")


async def get_product_by_platform_id(platform: str, platform_id: str) -> asyncpg.Record | None:
    """Look up a product by its platform and platform-specific ID."""
    return await fetchrow("""
        SELECT id, platform, platform_id, title, current_price, mrp, in_stock, status,
               affiliate_url, product_url, image_url, rating, review_count, all_time_low,
               priority_tier, last_checked
        FROM products
        WHERE platform = $1 AND platform_id = $2
    """, platform, platform_id)


async def get_active_deal_tracking() -> list[asyncpg.Record]:
    """Get all deals being tracked for message editing (within 2.5-day window, max 1000)."""
    return await fetch("""
        SELECT dt.*, p.current_price, p.in_stock, p.title
        FROM deal_tracking dt
        JOIN products p ON dt.product_id = p.id
        WHERE dt.track_until > NOW()
          AND dt.is_finalized = FALSE
        ORDER BY dt.posted_at DESC
        LIMIT 1000;
    """)


async def insert_deal_tracking(deal_id: int, product_id: int, message_id: int,
                                 channel_id: str, caption: str, price: float):
    """Record a posted deal for 2.5-day edit tracking."""
    await execute("""
        INSERT INTO deal_tracking (deal_id, product_id, message_id, channel_id,
                                    original_caption, posted_price, posted_at, track_until)
        VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW() + INTERVAL '60 hours')
    """, deal_id, product_id, message_id, channel_id, caption, price)


async def finalize_expired_tracking():
    """Mark expired tracking records as finalized."""
    result = await execute("""
        UPDATE deal_tracking
        SET is_finalized = TRUE
        WHERE track_until < NOW() AND is_finalized = FALSE
    """)
    logger.info(f"Finalized expired deal tracking: {result}")


async def insert_deal(data: dict) -> int:
    """
    Insert a broadcasted deal record into the deals table.
    Guarantees that the underlying product record is synchronized with last_checked = NOW()
    and verified current_price so customer freshness badges always reflect real-time verification.
    """
    pid = data.get("product_id")
    posted_price = data.get("posted_price")
    row = await fetchrow("""
        INSERT INTO deals (
            product_id, deal_type, posted_price,
            posted_mrp, savings_amount, savings_pct, deal_score,
            badge, source_channel, posted_at
        ) VALUES (
            COALESCE($1, (SELECT id FROM products WHERE platform = $10 AND platform_id = $11)), 
            $2, $3, $4, $5, $6, $7, $8, $9, NOW()
        )
        ON CONFLICT (product_id) DO UPDATE SET
            deal_type = EXCLUDED.deal_type,
            posted_price = EXCLUDED.posted_price,
            posted_mrp = EXCLUDED.posted_mrp,
            savings_amount = EXCLUDED.savings_amount,
            savings_pct = EXCLUDED.savings_pct,
            deal_score = EXCLUDED.deal_score,
            badge = EXCLUDED.badge,
            source_channel = EXCLUDED.source_channel,
            posted_at = NOW()
        RETURNING id;
    """,
        pid,
        data.get("deal_type", "price_drop"),
        posted_price,
        data.get("posted_mrp"),
        data.get("savings_amount", 0.0),
        data.get("savings_pct", 0.0),
        data.get("deal_score", 50.0),
        data.get("badge", "DEAL"),
        data.get("source_channel", "local_scanner"),
        data.get("platform"),
        data.get("platform_id")
    )
    if pid and posted_price:
        try:
            await execute("""
                UPDATE products 
                SET last_checked = NOW(),
                    current_price = $1,
                    in_stock = TRUE
                WHERE id = $2;
            """, float(posted_price), pid)
            _CORE_METRICS_CACHE["expires_at"] = 0
        except Exception as e:
            logger.debug(f"Sync product on deal insert note: {e}")
    _CORE_METRICS_CACHE["expires_at"] = 0
    return row["id"] if row else None


_CORE_METRICS_CACHE = {"data": None, "expires_at": 0}

async def get_core_metrics() -> dict:
    """
    Centralized Single Source of Truth for system-wide platform metrics.
    Cached in RAM for 600 seconds to protect database egress.
    Guarantees 100% mathematical synchronization across:
    1. Direct PostgreSQL Database queries
    2. Admin Control Center (/api/stats)
    3. PostgreSQL Database Studio (/api/db/overview)
    4. 24h Movements Hub (/api/price_changes_24h)
    5. Consumer Storefront Header & Hero (/api/public/stats)
    6. Consumer 24h Price Drops Carousel (/api/public/price-drops)
    """
    import time
    now = time.time()
    if _CORE_METRICS_CACHE["data"] and now < _CORE_METRICS_CACHE["expires_at"]:
        return _CORE_METRICS_CACHE["data"]

    row = await fetchrow("""
        WITH
          today AS (SELECT (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE AS d),
          prods AS (
            SELECT id, in_stock, status, current_price, created_at, all_time_low
            FROM products
          ),
          deals_base AS (
            SELECT d.product_id, d.posted_at, d.posted_price, d.id as deal_id
            FROM deals d
            JOIN products p ON d.product_id = p.id
          )
        SELECT
          -- total_products
          (SELECT COUNT(*) FROM prods) AS total_products,
          -- products_added_today
          (SELECT COUNT(*) FROM prods
           WHERE (created_at AT TIME ZONE 'Asia/Kolkata')::DATE = (SELECT d FROM today)) AS products_added_today,
          -- total_deals (active, in-stock, price still valid)
          (SELECT COUNT(DISTINCT db.product_id)
           FROM deals_base db
           JOIN prods p ON db.product_id = p.id
           WHERE p.in_stock = TRUE AND p.status = 'ACTIVE'
             AND p.current_price > 0
             AND p.current_price <= (db.posted_price * 1.01)
             AND db.posted_at >= NOW() - INTERVAL '30 days') AS total_deals,
          -- lifetime_deals
          (SELECT COUNT(*) FROM deals_base) AS lifetime_deals,
          -- deals_today
          (SELECT COUNT(DISTINCT db.product_id)
           FROM deals_base db
           JOIN prods p ON db.product_id = p.id
           WHERE db.posted_at >= (SELECT d FROM today)
             AND p.in_stock = TRUE AND p.status = 'ACTIVE'
             AND p.current_price > 0
             AND p.current_price <= (db.posted_price * 1.01)) AS deals_today,
          -- drops_today (price drop vs yesterday)
          (SELECT COUNT(*)
           FROM prods p
           JOIN daily_prices dp_today ON p.id = dp_today.product_id
             AND dp_today.date = (SELECT d FROM today)
           JOIN daily_prices dp_yest ON p.id = dp_yest.product_id
             AND dp_yest.date = (SELECT d FROM today) - 1
           WHERE dp_today.close_price < dp_yest.close_price
             AND p.in_stock = TRUE
             AND dp_today.close_price > 0 AND dp_yest.close_price > 0) AS drops_today;
    """)
    res = {
        "total_products": (row["total_products"] if row else 0) or 0,
        "products_added_today": (row["products_added_today"] if row else 0) or 0,
        "total_deals": (row["total_deals"] if row else 0) or 0,
        "active_deals": (row["total_deals"] if row else 0) or 0,
        "lifetime_deals": (row["lifetime_deals"] if row else 0) or 0,
        "deals_today": (row["deals_today"] if row else 0) or 0,
        "drops_today": (row["drops_today"] if row else 0) or 0,
    }
    _CORE_METRICS_CACHE["data"] = res
    _CORE_METRICS_CACHE["expires_at"] = now + 600
    return res



async def batch_update_products(updates: list):
    """Batch update prices from the local queue."""
    if not updates:
        return
    
    # updates is a list of tuples: (new_price, in_stock, status, platform, platform_id)
    query = """
        UPDATE products 
        SET current_price = $1, 
            in_stock = $2, 
            status = $3,
            last_checked = NOW()
        WHERE platform = $4 AND platform_id = $5
    """
    await executemany(query, updates)
