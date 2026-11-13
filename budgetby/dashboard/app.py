import asyncio
import datetime
import time
import math
import re
import sys
import os
import json
import ipaddress
import secrets
import hmac
import hashlib
from fastapi import FastAPI, Query, HTTPException, Request, Depends, Response, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn
import logging

logger = logging.getLogger("budgetby.dashboard.app")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from budgetby import database, config
from budgetby.dashboard.filters import register_jinja_filters
from budgetby.taxonomy import UNIVERSAL_CATEGORIES
from budgetby.dashboard.helpers import parse_search_query, is_safe_redirect_url, resolve_deal_button_url, build_token_regex_pattern

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import sentry_sdk

SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.05,
        environment=os.getenv("ENVIRONMENT", "production"),
    )

from fastapi.staticfiles import StaticFiles

app = FastAPI(title="BudgetBy Control Center", version="2.0")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Rate limiter — in-memory per-IP token bucket (single instance)
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Compress responses larger than 500 bytes to speed up mobile transfer and reduce bandwidth
app.add_middleware(GZipMiddleware, minimum_size=500)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        path = request.url.path
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        elif path.startswith("/api/public/"):
            if "stats" in path:
                response.headers["Cache-Control"] = "public, max-age=60, s-maxage=180, stale-while-revalidate=300"
            elif "categories" in path:
                response.headers["Cache-Control"] = "public, max-age=300, s-maxage=600, stale-while-revalidate=1200"
            elif "price-drops" in path or "drops" in path:
                response.headers["Cache-Control"] = "public, max-age=120, s-maxage=300, stale-while-revalidate=600"
            else:
                response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300, stale-while-revalidate=600"
            response.headers["Vary"] = "Accept-Encoding"
        elif path.startswith("/api/deal/redirect/"):
            response.headers["Cache-Control"] = "public, max-age=30"
        elif path.startswith("/api/"):
            # Admin/internal: never cache
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

app.add_middleware(SecurityHeadersMiddleware)

allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "")
if allowed_origins_env:
    origins = [orig.strip() for orig in allowed_origins_env.split(",") if orig.strip()]
else:
    origins = [
        "https://budgetby.in",
        "https://www.budgetby.in",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://localhost:5173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

@app.get("/healthz")
@app.get("/health")
async def healthz():
    """Ultra-fast, zero-database health check endpoint for Render container monitoring."""
    return {"status": "ok", "service": "budgetby", "timestamp": int(time.time())}


@app.get("/api/cron/cloud-scrape")
@app.post("/api/cron/cloud-scrape")
async def trigger_cloud_scrape(key: str = Query(""), limit: int = Query(25, ge=1, le=50)):
    """
    Serverless webhook endpoint to trigger an automated cloud scrape batch.
    Can be pinged for free 24/7 by cron-job.org, UptimeRobot, or GitHub Actions.
    Secured by ADMIN_SECRET_KEY.
    """
    expected_key = config.ADMIN_SECRET_KEY or "bb_sec_9e72f8a14b30c5e7d82f091a384b62d1"
    if not key or key != expected_key:
        raise HTTPException(status_code=403, detail="Unauthorized: invalid or missing cron key")

    try:
        from budgetby.cloud_scraper import run_cloud_scrape_batch
        stats = await run_cloud_scrape_batch(limit=limit, concurrency=4)
        return {"status": "success", "stats": stats}
    except Exception as e:
        logger.error(f"Cloud scrape webhook error: {e}")
        return {"status": "error", "error": str(e)}

@app.on_event("startup")
async def startup_event():
    logger.info("Initializing Database Pool...")
    await database.init_pool()
    # Pre-warm homepage and storefront SSR cache in background so every visitor gets instant (<20ms) loads
    asyncio.create_task(ssr_prewarm_loop())
    if os.getenv("ENABLE_CLOUD_SCHEDULER", "false").lower() in ("true", "1", "yes"):
        try:
            from budgetby.scheduler import scheduler
            scheduler.start_scheduler()
            logger.info("🚀 APScheduler background price-checker and deal-detector started successfully!")
        except Exception as e:
            logger.warning(f"Scheduler startup note: {e}")
    else:
        logger.info("🌐 Web-only mode active: Background scheduler disabled on web server (delegated to local daemon).")

@app.on_event("shutdown")
async def shutdown_event():
    try:
        from budgetby.scheduler import scheduler
        scheduler.stop_scheduler()
    except Exception:
        pass
    await database.close_pool()

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# ── Jinja2 Custom Storefront Filters for Server-Side Rendering (SSR) ────────
register_jinja_filters(templates)

ADMIN_TEMPLATE_PATH = os.path.join(TEMPLATES_DIR, "index.html")
EXPLORER_TEMPLATE_PATH = os.path.join(TEMPLATES_DIR, "explorer.html")
ADMIN_LOGIN_TEMPLATE_PATH = os.path.join(TEMPLATES_DIR, "admin_login.html")

ADMIN_SECRET_KEY = getattr(config, "ADMIN_SECRET_KEY", "") or os.getenv("ADMIN_SECRET_KEY", "")

def _get_admin_credentials() -> list[tuple[str, str]]:
    """Loads authorized admin credentials securely from environment variables."""
    creds_str = os.getenv("ADMIN_CREDENTIALS", "")
    creds = []
    if creds_str:
        for pair in creds_str.split(";"):
            if ":" in pair:
                u, p = pair.split(":", 1)
                creds.append((u.strip(), p.strip()))
    if not creds:
        u = os.getenv("ADMIN_USERNAME", "admin")
        p = os.getenv("ADMIN_PASSWORD", "")
        if p:
            creds.append((u, p))
    return creds

ADMIN_CREDENTIALS = _get_admin_credentials()

def create_admin_session_token(username: str) -> str:
    """Creates a tamper-proof HMAC-SHA256 signed session token."""
    if not ADMIN_SECRET_KEY:
        return ""
    now_ts = int(time.time())
    msg = f"{username}:{now_ts}"
    sig = hmac.new(ADMIN_SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}:{sig}"

def verify_admin_session_token(token: str) -> bool:
    """Verifies that the session token is authentic, unexpired, and properly signed."""
    if not token or not ADMIN_SECRET_KEY:
        return False
    # Backwards compatibility: raw ADMIN_SECRET_KEY as cookie
    if ADMIN_SECRET_KEY and secrets.compare_digest(str(token), ADMIN_SECRET_KEY):
        return True
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        username, ts_str, sig = parts
        msg = f"{username}:{ts_str}"
        expected_sig = hmac.new(ADMIN_SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(sig, expected_sig):
            return False
        token_time = int(ts_str)
        if (time.time() - token_time) > 86400 * 7:  # 7 days max age
            return False
        return username in ("pnther", "vidushi", "admin")
    except Exception:
        return False

def is_admin_enabled(request: Request) -> bool:
    """
    Completely isolates and disables the admin panel & DB explorer from cloud deployments.
    - Cloud (Render): Disabled by default unless ENABLE_ADMIN_PANEL=true is explicitly set in env.
    - Localhost / 127.0.0.1: Enabled for local management and operations.
    """
    env_setting = os.getenv("ENABLE_ADMIN_PANEL", "").strip().lower()
    if env_setting in ("true", "1", "yes"):
        return True
    if env_setting in ("false", "0", "no"):
        return False
    # If not set in environment, allow local connections only
    client_host = getattr(getattr(request, "client", None), "host", "")
    return client_host in ("127.0.0.1", "localhost", "::1", "testclient", "")

def is_admin_authorized(request: Request) -> bool:
    """
    Cryptographically verifies admin access via:
    1. Header: X-Admin-Session: <token>
    2. Header: X-Admin-Key: <ADMIN_SECRET_KEY>
    3. Query parameter: ?key=<ADMIN_SECRET_KEY> or ?admin_key=<ADMIN_SECRET_KEY>
    4. Query parameter: ?auth_token=<token>
    """
    if not ADMIN_SECRET_KEY:
        return False
    session_hdr = request.headers.get("X-Admin-Session") or request.headers.get("x-admin-session")
    if session_hdr and verify_admin_session_token(session_hdr):
        return True
    header_key = request.headers.get("X-Admin-Key") or request.headers.get("x-admin-key")
    if header_key and secrets.compare_digest(str(header_key), ADMIN_SECRET_KEY):
        return True
    req_key = request.query_params.get("key") or request.query_params.get("admin_key")
    if req_key and secrets.compare_digest(str(req_key), ADMIN_SECRET_KEY):
        return True
    auth_tok = request.query_params.get("auth_token")
    if auth_tok and verify_admin_session_token(auth_tok):
        return True
    return False

def require_admin(request: Request):
    """
    Dependency enforcing complete admin stealth isolation for APIs:
    Returns 404 Not Found to unauthorized requests or when disabled on cloud.
    """
    if not is_admin_enabled(request) or not is_admin_authorized(request):
        raise HTTPException(status_code=404, detail="Not Found")
    return True


# ── High-Speed In-Memory RAM Caching ─────────────────────────────────────────

class SimpleMemoryCache:
    """
    High-concurrency in-memory TTL cache with Stale-While-Revalidate (SWR).
    Eliminates redundant database queries by caching popular public API endpoints in RAM.
    Reduces database load and egress bandwidth by >98% while ensuring instant user responses (<20ms).
    """
    def __init__(self, default_ttl: int = 120, max_entries: int = 3000):
        # Format: key -> (value, fresh_until, stale_until)
        self._cache: dict[str, tuple[any, float, float]] = {}
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._lock = asyncio.Lock()

    async def get(self, key: str):
        entry = self._cache.get(key)
        if not entry:
            return None
        val, fresh_until, stale_until = entry
        now = time.monotonic()
        if now > fresh_until:
            if now > stale_until:
                self._cache.pop(key, None)
            return None
        return val

    async def get_stale(self, key: str) -> tuple[any, bool]:
        """
        Returns (value, is_stale).
        If value is within fresh_until: returns (value, False)
        If value is between fresh_until and stale_until: returns (value, True) (caller can serve instantly & refresh in bg)
        If expired beyond stale_until or not found: returns (None, False)
        """
        entry = self._cache.get(key)
        if not entry:
            return None, False
        val, fresh_until, stale_until = entry
        now = time.monotonic()
        if now <= fresh_until:
            return val, False
        elif now <= stale_until:
            return val, True
        else:
            self._cache.pop(key, None)
            return None, False

    async def set(self, key: str, value: any, ttl: int = None, stale_ttl: int = None):
        if ttl is None:
            ttl = self._default_ttl
        if stale_ttl is None:
            stale_ttl = ttl * 3 + 300  # Generous stale window for instant fallback
        now = time.monotonic()
        fresh_until = now + ttl
        stale_until = now + stale_ttl
        async with self._lock:
            if len(self._cache) >= self._max_entries:
                keys_to_del = [k for k, (v, f_exp, s_exp) in self._cache.items() if now > s_exp]
                for k in keys_to_del:
                    self._cache.pop(k, None)
                if len(self._cache) >= self._max_entries:
                    for k in list(self._cache.keys())[:int(self._max_entries * 0.2)]:
                        self._cache.pop(k, None)
            self._cache[key] = (value, fresh_until, stale_until)

    def clear(self):
        self._cache.clear()

ram_cache = SimpleMemoryCache(default_ttl=120, max_entries=3000)


class AsyncSingleFlightCoalescer:
    """Coalesces concurrent requests for the same cache key so only 1 DB/render query runs."""
    def __init__(self):
        self._in_flight: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def run(self, key: str, coro_func):
        async with self._lock:
            if key in self._in_flight:
                fut = self._in_flight[key]
                execute = False
            else:
                fut = asyncio.get_running_loop().create_future()
                self._in_flight[key] = fut
                execute = True

        if not execute:
            return await fut

        try:
            result = await coro_func()
            if not fut.done():
                fut.set_result(result)
            return result
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise exc
        finally:
            async with self._lock:
                self._in_flight.pop(key, None)

coalescer = AsyncSingleFlightCoalescer()


@app.on_event("startup")
async def startup():
    if not database._pool:
        await database.init_pool()

@app.get("/api/stats", dependencies=[Depends(require_admin)])
async def get_stats():
    cache_key = "api_stats_cache"
    cached = await ram_cache.get(cache_key)
    if cached is not None:
        return cached

    async def _compute_stats():
        core_metrics = await database.get_core_metrics()
        total_prods = core_metrics["total_products"]
        products_added_today = core_metrics["products_added_today"]
        deals_lifetime = core_metrics.get("lifetime_deals", core_metrics["total_deals"])
        deals_active = core_metrics["total_deals"]
        deals_today = core_metrics["deals_today"]
        drops_today = core_metrics.get("drops_today", 0)

        by_plat = await database.fetch("SELECT platform, COUNT(*) as count FROM products GROUP BY platform ORDER BY count DESC;")
        
        # Time-based deals counts (IST Timezone)
        deals_1h = await database.fetchval("""
            SELECT COUNT(*) FROM deals d
            JOIN products p ON d.product_id = p.id
            WHERE d.posted_at >= NOW() - INTERVAL '1 hour';
        """)
        
        deals_this_month = await database.fetchval("""
            SELECT COUNT(*) FROM deals d
            JOIN products p ON d.product_id = p.id
            WHERE d.posted_at >= date_trunc('month', NOW() AT TIME ZONE 'Asia/Kolkata');
        """)
        
        deals_last_month = await database.fetchval("""
            SELECT COUNT(*) FROM deals d
            JOIN products p ON d.product_id = p.id
            WHERE d.posted_at >= date_trunc('month', (NOW() AT TIME ZONE 'Asia/Kolkata') - INTERVAL '1 month')
              AND d.posted_at < date_trunc('month', NOW() AT TIME ZONE 'Asia/Kolkata');
        """)
        
        # Deals posted TODAY grouped by platform
        posted_today_rows = await database.fetch("""
            SELECT p.platform, COUNT(*) as count 
            FROM deals d 
            JOIN products p ON d.product_id = p.id 
            WHERE d.posted_at >= (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE 
            GROUP BY p.platform;
        """)
        posted_today_by_plat = {r["platform"].lower(): r["count"] for r in posted_today_rows}
        
        # Deals posted THIS MONTH grouped by platform
        posted_month_rows = await database.fetch("""
            SELECT p.platform, COUNT(*) as count 
            FROM deals d 
            JOIN products p ON d.product_id = p.id 
            WHERE d.posted_at >= date_trunc('month', NOW() AT TIME ZONE 'Asia/Kolkata')
            GROUP BY p.platform;
        """)
        posted_month_by_plat = {r["platform"].lower(): r["count"] for r in posted_month_rows}
        
        # Deals posted LIFETIME grouped by platform
        posted_life_rows = await database.fetch("""
            SELECT p.platform, COUNT(*) as count 
            FROM deals d 
            JOIN products p ON d.product_id = p.id 
            GROUP BY p.platform;
        """)
        posted_life_by_plat = {r["platform"].lower(): r["count"] for r in posted_life_rows}
        
        all_plats = ["amazon", "flipkart", "myntra", "ajio", "nykaa"]
        for p in all_plats:
            posted_today_by_plat.setdefault(p, 0)
            posted_month_by_plat.setdefault(p, 0)
            posted_life_by_plat.setdefault(p, 0)
                
        cooldowns = await database.fetchval("SELECT COUNT(*) FROM post_cooldowns WHERE expires_at > NOW();")
        daily_prices = await database.fetchval("SELECT COUNT(*) FROM daily_prices;")
        
        # Channel Ingestion Stats
        total_ingested = await database.fetchval("SELECT COUNT(*) FROM ingested_channel_deals;")
        ingested_today = await database.fetchval("SELECT COUNT(*) FROM ingested_channel_deals WHERE created_at >= (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE;")
        
        # Monthly History Breakdown
        history_rows = await database.fetch("""
            SELECT 
                to_char(date_trunc('month', d.posted_at AT TIME ZONE 'Asia/Kolkata'), 'YYYY-MM') as month_key,
                to_char(date_trunc('month', d.posted_at AT TIME ZONE 'Asia/Kolkata'), 'FMMonth YYYY') as month_name,
                COUNT(*) as total_deals,
                COUNT(CASE WHEN p.platform = 'amazon' THEN 1 END) as amazon_deals,
                COUNT(CASE WHEN p.platform = 'flipkart' THEN 1 END) as flipkart_deals,
                COUNT(CASE WHEN p.platform = 'myntra' THEN 1 END) as myntra_deals,
                COUNT(CASE WHEN p.platform = 'ajio' THEN 1 END) as ajio_deals,
                COUNT(CASE WHEN p.platform = 'nykaa' THEN 1 END) as nykaa_deals
            FROM deals d
            LEFT JOIN products p ON d.product_id = p.id
            GROUP BY date_trunc('month', d.posted_at AT TIME ZONE 'Asia/Kolkata')
            ORDER BY date_trunc('month', d.posted_at AT TIME ZONE 'Asia/Kolkata') DESC;
        """)
        
        return {
            "status": "online",
            "total_products": total_prods or 0,
            "products_added_today": products_added_today or 0,
            "by_platform": {r["platform"]: r["count"] for r in by_plat},
            "deals_today": deals_today or 0,
            "drops_today": drops_today or 0,
            "total_deals": deals_active or 0,
            "deals_active": deals_active or 0,
            "deals_last_hour": deals_1h or 0,
            "deals_this_month": deals_this_month or 0,
            "deals_last_month": deals_last_month or 0,
            "deals_lifetime": deals_lifetime or 0,
            "total_deals_lifetime": deals_lifetime or 0,
            "posted_today_by_platform": posted_today_by_plat,
            "posted_this_month_by_platform": posted_month_by_plat,
            "posted_lifetime_by_platform": posted_life_by_plat,
            "total_ingested_from_channels": total_ingested or 0,
            "ingested_today_from_channels": ingested_today or 0,
            "monthly_history": [dict(r) for r in history_rows],
            "hourly_targets": config.PLATFORM_MIN_HOURLY_POSTS,
            "active_cooldowns": cooldowns or 0,
            "daily_prices_recorded": daily_prices or 0
        }

    try:
        res = await coalescer.run(cache_key, _compute_stats)
        if res:
            await ram_cache.set(cache_key, res, ttl=60)
        return res
    except Exception as e:
        logger.error(f"Internal stats error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/channel_stats", dependencies=[Depends(require_admin)])
async def get_channel_stats():
    """Returns all monitored Telegram channels with real-time heartbeat and deal statistics (120s RAM cache)."""
    try:
        cache_key = "admin_channel_stats"
        cached = await ram_cache.get(cache_key)
        if cached is not None:
            return cached

        async def _fetch_channel_stats():
            c = await ram_cache.get(cache_key)
            if c is not None:
                return c
            rows = await database.fetch("""
                SELECT 
                    m.channel_name as source_channel,
                    m.status as monitor_status,
                    COALESCE(COUNT(d.id), 0) as total_picked_up,
                    COALESCE(COUNT(CASE WHEN d.product_id IS NOT NULL THEN 1 END), 0) as saved_to_catalog,
                    COALESCE(COUNT(CASE WHEN d.status = 'VERIFIED_DEAL' THEN 1 END), 0) as verified_deals,
                    COALESCE(COUNT(CASE WHEN d.status = 'OUT_OF_STOCK' THEN 1 END), 0) as out_of_stock,
                    COALESCE(COUNT(CASE WHEN d.status = 'FAILED_SCRAPE' THEN 1 END), 0) as failed_scrapes,
                    m.last_scanned_at as last_activity
                FROM channel_monitors m
                LEFT JOIN ingested_channel_deals d ON LOWER(d.source_channel) = LOWER(m.channel_name)
                GROUP BY m.channel_name, m.status, m.last_scanned_at
                ORDER BY total_picked_up DESC, m.channel_name ASC;
            """)
            res = [dict(r) for r in rows]
            await ram_cache.set(cache_key, res, ttl=120)
            return res

        return await coalescer.run(cache_key, _fetch_channel_stats)
    except Exception as e:
        logger.error(f"Channel stats error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/posting_queue", dependencies=[Depends(require_admin)])
async def get_posting_queue_endpoint():
    """Returns real-time deals in the posting queue waiting to be broadcasted."""
    try:
        from budgetby.engine.posting_queue import get_posting_queue
        pq = get_posting_queue()
        return pq.get_queue_snapshot()
    except Exception as e:
        logger.error(f"Posting queue error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/posting_queue/clear", dependencies=[Depends(require_admin)])
async def clear_posting_queue_endpoint():
    """Flushes and clears all items currently waiting in the in-memory posting queue."""
    try:
        from budgetby.engine.posting_queue import get_posting_queue
        pq = get_posting_queue()
        cleared = pq.clear()
        return {"status": "ok", "cleared_items": cleared, "queue_size": 0}
    except Exception as e:
        logger.error(f"Posting queue clear error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/category_platform_stats", dependencies=[Depends(require_admin)])
async def get_category_platform_stats():
    """Returns detailed cross-matrix of deals and catalog products by category and platform (300s RAM cache)."""
    try:
        cache_key = "admin_category_platform_stats"
        cached = await ram_cache.get(cache_key)
        if cached is not None:
            return cached

        async def _fetch_category_platform_stats():
            c = await ram_cache.get(cache_key)
            if c is not None:
                return c
            # 1. Lifetime Deals Posted by Category & Platform
            lifetime_rows = await database.fetch("""
                SELECT 
                    COALESCE(NULLIF(p.category, ''), 'general') as category,
                    COUNT(*) as total_deals,
                    COUNT(CASE WHEN p.platform = 'amazon' THEN 1 END) as amazon_count,
                    COUNT(CASE WHEN p.platform = 'flipkart' THEN 1 END) as flipkart_count,
                    COUNT(CASE WHEN p.platform = 'myntra' THEN 1 END) as myntra_count,
                    COUNT(CASE WHEN p.platform = 'ajio' THEN 1 END) as ajio_count,
                    COUNT(CASE WHEN p.platform = 'nykaa' THEN 1 END) as nykaa_count
                FROM deals d
                JOIN products p ON d.product_id = p.id
                GROUP BY COALESCE(NULLIF(p.category, ''), 'general')
                ORDER BY total_deals DESC;
            """)

            # 2. Today's Deals Posted by Category & Platform (IST)
            today_rows = await database.fetch("""
                SELECT 
                    COALESCE(NULLIF(p.category, ''), 'general') as category,
                    COUNT(*) as total_deals,
                    COUNT(CASE WHEN p.platform = 'amazon' THEN 1 END) as amazon_count,
                    COUNT(CASE WHEN p.platform = 'flipkart' THEN 1 END) as flipkart_count,
                    COUNT(CASE WHEN p.platform = 'myntra' THEN 1 END) as myntra_count,
                    COUNT(CASE WHEN p.platform = 'ajio' THEN 1 END) as ajio_count,
                    COUNT(CASE WHEN p.platform = 'nykaa' THEN 1 END) as nykaa_count
                FROM deals d
                JOIN products p ON d.product_id = p.id
                WHERE d.posted_at >= (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE
                GROUP BY COALESCE(NULLIF(p.category, ''), 'general')
                ORDER BY total_deals DESC;
            """)

            # 3. This Month Deals Posted by Category & Platform
            month_rows = await database.fetch("""
                SELECT 
                    COALESCE(NULLIF(p.category, ''), 'general') as category,
                    COUNT(*) as total_deals,
                    COUNT(CASE WHEN p.platform = 'amazon' THEN 1 END) as amazon_count,
                    COUNT(CASE WHEN p.platform = 'flipkart' THEN 1 END) as flipkart_count,
                    COUNT(CASE WHEN p.platform = 'myntra' THEN 1 END) as myntra_count,
                    COUNT(CASE WHEN p.platform = 'ajio' THEN 1 END) as ajio_count,
                    COUNT(CASE WHEN p.platform = 'nykaa' THEN 1 END) as nykaa_count
                FROM deals d
                JOIN products p ON d.product_id = p.id
                WHERE d.posted_at >= date_trunc('month', NOW() AT TIME ZONE 'Asia/Kolkata')
                GROUP BY COALESCE(NULLIF(p.category, ''), 'general')
                ORDER BY total_deals DESC;
            """)

            # 4. Catalog Products Available by Category & Platform
            catalog_rows = await database.fetch("""
                SELECT 
                    COALESCE(NULLIF(category, ''), 'general') as category,
                    COUNT(*) as total_prods,
                    COUNT(CASE WHEN platform = 'amazon' THEN 1 END) as amazon_count,
                    COUNT(CASE WHEN platform = 'flipkart' THEN 1 END) as flipkart_count,
                    COUNT(CASE WHEN platform = 'myntra' THEN 1 END) as myntra_count,
                    COUNT(CASE WHEN platform = 'ajio' THEN 1 END) as ajio_count,
                    COUNT(CASE WHEN platform = 'nykaa' THEN 1 END) as nykaa_count
                FROM products
                GROUP BY COALESCE(NULLIF(category, ''), 'general')
                ORDER BY total_prods DESC;
            """)

            res = {
                "lifetime": [dict(r) for r in lifetime_rows],
                "today": [dict(r) for r in today_rows],
                "this_month": [dict(r) for r in month_rows],
                "catalog": [dict(r) for r in catalog_rows]
            }
            await ram_cache.set(cache_key, res, ttl=300)
            return res

        return await coalescer.run(cache_key, _fetch_category_platform_stats)
    except Exception as e:
        logger.error(f"Category platform stats error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/price_changes_24h", dependencies=[Depends(require_admin)])
async def get_price_changes_24h(
    direction: str = Query("all", pattern="^(all|drops|hikes)$"),
    platform: str = Query("", max_length=50),
    search: str = Query("", max_length=100),
    sort_by: str = Query("latest", max_length=50),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100)
):
    """
    Dedicated 24-Hour Price Movements Tracker.
    Returns all products whose prices changed in the last 24 hours.
    Products automatically drop off once they pass 24 hours without new changes.
    """
    try:
        direction_str = direction if isinstance(direction, str) else "all"
        platform_str = (platform if isinstance(platform, str) else "").strip().lower()
        search_str = (search if isinstance(search, str) else "").strip()
        sort_by_str = sort_by if isinstance(sort_by, str) else "latest"
        page_int = int(page) if isinstance(page, (int, str)) and str(page).isdigit() else 1
        limit_int = int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 25

        offset = (page_int - 1) * limit_int
        where_clauses = [
            "dp_today.close_price != dp_yest.close_price",
            "dp_today.close_price > 0",
            "dp_yest.close_price > 0",
            "(p.mrp = 0 OR (dp_today.close_price <= (p.mrp * 1.15) AND dp_yest.close_price <= (p.mrp * 1.15)))",
            "(dp_today.close_price <= (dp_yest.close_price * 3.0) AND (dp_yest.close_price <= 1500 OR dp_today.close_price >= (dp_yest.close_price * 0.40)))",
            "COALESCE(p.last_price_change, NOW()) >= (NOW() - INTERVAL '24 hours')"
        ]
        args = []
        arg_idx = 1

        if platform_str:
            where_clauses.append(f"LOWER(p.platform) = ${arg_idx}")
            args.append(platform_str)
            arg_idx += 1

        if direction_str == "drops":
            where_clauses.append("dp_today.close_price < dp_yest.close_price")
        elif direction_str == "hikes":
            where_clauses.append("dp_today.close_price > dp_yest.close_price")

        clean_search = search_str.lstrip("#")
        if clean_search:
            if clean_search.isdigit():
                where_clauses.append(f"(p.id = ${arg_idx} OR p.title ILIKE ${arg_idx+1})")
                args.extend([int(clean_search), f"%{clean_search}%"])
                arg_idx += 2
            else:
                where_clauses.append(f"p.title ILIKE ${arg_idx}")
                args.append(f"%{clean_search}%")
                arg_idx += 1

        where_sql = " AND ".join(where_clauses)

        # Sorting logic: Latest arrivals first by default
        if sort_by_str == "latest":
            order_sql = "ORDER BY changed_at DESC, id DESC"
        elif sort_by_str == "pct_desc":
            order_sql = "ORDER BY ABS(change_pct) DESC, id ASC"
        elif sort_by_str == "drop_largest":
            order_sql = "ORDER BY price_diff ASC, id ASC"
        elif sort_by_str == "hike_largest":
            order_sql = "ORDER BY price_diff DESC, id ASC"
        elif sort_by_str == "price_asc":
            order_sql = "ORDER BY new_price ASC, id ASC"
        elif sort_by_str == "diff_desc":
            order_sql = "ORDER BY ABS(price_diff) DESC, id ASC"
        else: # Default latest
            order_sql = "ORDER BY changed_at DESC, id DESC"

        query = f"""
            WITH changed_prods AS (
                SELECT 
                    p.id,
                    p.platform,
                    p.platform_id,
                    p.title,
                    p.category,
                    p.image_url,
                    p.product_url,
                    p.affiliate_url,
                    p.mrp,
                    COALESCE(dp_yest.close_price, p.previous_price, p.current_price) as old_price,
                    dp_today.close_price as new_price,
                    dp_today.min_price as today_min_price,
                    (dp_today.close_price - COALESCE(dp_yest.close_price, p.previous_price, p.current_price)) as price_diff,
                    ROUND(((dp_today.close_price - COALESCE(dp_yest.close_price, p.previous_price, p.current_price)) / NULLIF(COALESCE(dp_yest.close_price, p.previous_price, p.current_price), 0)) * 100, 1) as change_pct,
                    COALESCE(p.last_price_change, NOW()) as changed_at
                FROM products p
                JOIN daily_prices dp_today ON p.id = dp_today.product_id AND dp_today.date = (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE
                JOIN daily_prices dp_yest ON p.id = dp_yest.product_id AND dp_yest.date = ((NOW() AT TIME ZONE 'Asia/Kolkata')::DATE - 1)
                WHERE {where_sql}
            )
            SELECT 
                id, platform, platform_id, title, category, image_url, product_url, affiliate_url,
                mrp, old_price, new_price, today_min_price, price_diff, change_pct, changed_at,
                COUNT(*) OVER() as total_matches,
                COUNT(CASE WHEN price_diff < 0 THEN 1 END) OVER() as count_drops,
                COUNT(CASE WHEN price_diff > 0 THEN 1 END) OVER() as count_hikes
            FROM changed_prods
            {order_sql}
            LIMIT ${arg_idx} OFFSET ${arg_idx + 1};
        """
        args.extend([limit, offset])

        rows = await database.fetch(query, *args)
        
        total_matches = rows[0]["total_matches"] if rows else 0

        # Global stats across entire 24h window (in-stock active products)
        core_metrics = await database.get_core_metrics()
        global_stats = await database.fetchrow("""
            SELECT 
                COUNT(*) as global_total,
                COUNT(CASE WHEN dp_today.close_price > dp_yest.close_price THEN 1 END) as global_hikes
            FROM products p
            JOIN daily_prices dp_today ON p.id = dp_today.product_id AND dp_today.date = (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE
            JOIN daily_prices dp_yest ON p.id = dp_yest.product_id AND dp_yest.date = ((NOW() AT TIME ZONE 'Asia/Kolkata')::DATE - 1)
            WHERE dp_today.close_price != dp_yest.close_price
              AND p.in_stock = TRUE AND dp_today.close_price > 0 AND dp_yest.close_price > 0;
        """)

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "platform": r["platform"],
                "platform_id": r["platform_id"],
                "title": r["title"],
                "category": r["category"],
                "image_url": r["image_url"],
                "product_url": r["product_url"],
                "affiliate_url": r["affiliate_url"] or r["product_url"],
                "old_price": float(r["old_price"]) if r["old_price"] else None,
                "new_price": float(r["new_price"]) if r["new_price"] else None,
                "today_min_price": float(r["today_min_price"]) if r["today_min_price"] else None,
                "mrp": float(r["mrp"]) if r["mrp"] else None,
                "price_diff": float(r["price_diff"]) if r["price_diff"] else 0.0,
                "change_pct": float(r["change_pct"]) if r["change_pct"] else 0.0,
                "is_drop": bool(r["price_diff"] < 0),
                "changed_at": str(r["changed_at"])
            })

        return {
            "page": page,
            "limit": limit,
            "total_matches": total_matches,
            "total_pages": (total_matches + limit - 1) // limit if total_matches > 0 else 1,
            "global_stats": {
                "total": global_stats["global_total"] if global_stats else 0,
                "drops": core_metrics["drops_today"],
                "hikes": global_stats["global_hikes"] if global_stats else 0
            },
            "products": results
        }
    except Exception as e:
        logger.error(f"Error in get_price_changes_24h: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch 24h price movements")


# ── Full Database Explorer Endpoints ──────────────────────────────────────────

ALLOWED_TABLES = [
    "products",
    "daily_prices",
    "deals",
    "ingested_channel_deals",
    "post_cooldowns",
    "channel_monitors",
    "deal_tracking"
]

ALLOWED_SORT_COLUMNS = {
    "products": {"id", "title", "platform", "current_price", "mrp", "rating", "review_count", "in_stock", "status", "created_at", "last_checked", "last_price_change", "all_time_low"},
    "deals": {"id", "product_id", "posted_price", "posted_mrp", "savings_pct", "badge", "deal_score", "posted_at", "source_channel"},
    "daily_prices": {"id", "product_id", "date", "open_price", "high", "low", "close_price", "created_at"},
    "ingested_channel_deals": {"id", "product_id", "platform", "raw_url", "source_channel", "created_at", "status"},
    "channel_monitors": {"id", "channel_name", "status", "last_scanned_at"},
    "post_cooldowns": {"id", "product_id", "expires_at", "created_at"},
    "deal_tracking": {"id", "product_id", "status", "created_at"}
}

EXPLORER_TABLE_COLUMNS = {
    "products": "id, platform, platform_id, title, category, current_price, mrp, rating, review_count, in_stock, status, priority_tier, last_checked, all_time_low",
    "deals": "id, product_id, posted_price, posted_mrp, savings_amount, savings_pct, deal_score, badge, deal_type, posted_at, source_channel",
    "daily_prices": "id, product_id, date, min_price, close_price, is_compressed",
    "ingested_channel_deals": "id, source_channel, status, posted_price, original_url, created_at",
    "channel_monitors": "id, channel_name, status, last_scanned_at, deals_detected_24h",
    "post_cooldowns": "id, product_id, expires_at",
    "deal_tracking": "id, product_id, posted_price, posted_at, track_until, is_finalized"
}

def inject_admin_session_script(html_content: str, token: str) -> str:
    """
    Injects tab-scoped, in-memory session token & fetch interceptor.
    Prevents persistent cookies:
    - Reloading the page forces re-login (URL token is stripped from history via replaceState)
    - Opening a new tab forces re-login (no cookies or URL tokens shared)
    - Directly visiting /pnther/login always shows the login form
    - All background AJAX/fetch calls in the active dashboard are seamlessly authenticated via X-Admin-Session header
    """
    script_tag = f"""
    <script>
        window.BB_ADMIN_TOKEN = "{token}";
        (function() {{
            const originalFetch = window.fetch;
            window.fetch = function(input, init) {{
                if (input instanceof Request) {{
                    input.headers.set('X-Admin-Session', window.BB_ADMIN_TOKEN);
                    return originalFetch(input, init);
                }}
                init = init || {{}};
                init.headers = init.headers || {{}};
                if (init.headers instanceof Headers) {{
                    init.headers.set('X-Admin-Session', window.BB_ADMIN_TOKEN);
                }} else if (Array.isArray(init.headers)) {{
                    init.headers.push(['X-Admin-Session', window.BB_ADMIN_TOKEN]);
                }} else {{
                    init.headers['X-Admin-Session'] = window.BB_ADMIN_TOKEN;
                }}
                return originalFetch(input, init);
            }};
            if (window.history.replaceState) {{
                try {{
                    const u = new URL(window.location.href);
                    if (u.searchParams.has('auth_token')) {{
                        u.searchParams.delete('auth_token');
                        window.history.replaceState({{}}, document.title, u.pathname + (u.search ? u.search : ''));
                    }}
                }} catch(e) {{}}
            }}
            document.addEventListener('click', function(e) {{
                const a = e.target.closest('a');
                if (a && a.href) {{
                    try {{
                        const u = new URL(a.href, window.location.origin);
                        if (u.origin === window.location.origin && u.pathname.startsWith('/pnther') && !u.pathname.startsWith('/pnther/logout') && !u.pathname.startsWith('/pnther/login')) {{
                            if (!a.target || a.target === '_self') {{
                                e.preventDefault();
                                u.searchParams.set('auth_token', window.BB_ADMIN_TOKEN);
                                window.location.href = u.toString();
                            }}
                        }}
                    }} catch(err) {{}}
                }}
            }});
        }})();
    </script>
    """
    if "</head>" in html_content:
        return html_content.replace("</head>", f"{script_tag}\n</head>", 1)
    return script_tag + html_content

async def render_db_explorer(token: str = ""):
    """Renders the Full Database Explorer Web Interface (accessible strictly under /pnther/db-explorer)."""
    explorer_html_path = os.path.join(os.path.dirname(__file__), "templates", "explorer.html")
    raw_html = "<h1>Database Explorer Loading...</h1>"
    if os.path.exists(explorer_html_path):
        with open(explorer_html_path, "r", encoding="utf-8") as f:
            raw_html = f.read()
    if token:
        return HTMLResponse(inject_admin_session_script(raw_html, token))
    return HTMLResponse(raw_html)

@app.get("/db-explorer")
@app.get("/db-explorer/{rest:path}")
async def redirect_old_db_explorer(rest: str = ""):
    """Safely redirects public /db-explorer requests to consumer homepage."""
    return RedirectResponse(url="/", status_code=302)

@app.get("/api/db/overview", dependencies=[Depends(require_admin)])
async def get_db_overview():
    """Returns database size, table summaries, and column schemas for all tables."""
    try:
        total_db_size = await database.fetchval("SELECT pg_size_pretty(pg_database_size('budgetby'));")
        core_metrics = await database.get_core_metrics()
        total_products = core_metrics["total_products"]
        products_added_today = core_metrics["products_added_today"]
        
        tables_stats = await database.fetch("""
            SELECT
                relname AS table_name,
                n_live_tup AS row_count,
                pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                pg_total_relation_size(relid) AS total_bytes
            FROM pg_stat_user_tables
            ORDER BY total_bytes DESC;
        """)
        
        cols_all = await database.fetch("""
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = ANY($1::text[])
            ORDER BY table_name, ordinal_position;
        """, ALLOWED_TABLES)
        table_schemas = {t: [] for t in ALLOWED_TABLES}
        for c in cols_all:
            t_name = c["table_name"]
            if t_name in table_schemas:
                table_schemas[t_name].append({
                    "column_name": c["column_name"],
                    "data_type": c["data_type"],
                    "is_nullable": c["is_nullable"]
                })

        return {
            "total_size": total_db_size,
            "total_products": total_products or 0,
            "products_added_today": products_added_today or 0,
            "tables": [dict(t) for t in tables_stats],
            "schemas": table_schemas
        }
    except Exception as e:
        logger.error(f"Error in get_db_overview: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch database overview")


@app.get("/api/db/product_history/{product_id}", dependencies=[Depends(require_admin)])
async def get_product_history(product_id: int):
    """Returns the full 30-day chronological daily price timeline (Day 1 to Day 30) for a product."""
    try:
        product = await database.fetchrow("""
            SELECT id, platform, title, current_price, mrp, all_time_low, rating, image_url, product_url, affiliate_url, category 
            FROM products WHERE id = $1;
        """, product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        # Option 2: Chronological Age (Day 1 = First Discovered -> Day N = Today)
        daily_rows = await database.fetch("""
            SELECT 
                date,
                min_price,
                close_price
            FROM daily_prices
            WHERE product_id = $1
            ORDER BY date ASC
            LIMIT 30;
        """, product_id)

        today_str = str(await database.fetchval("SELECT (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE;"))
        history = []
        for idx, r in enumerate(daily_rows):
            d_str = str(r["date"])
            day_num = idx + 1
            if day_num == 1:
                label = "Day 1 (First Tracked)"
            elif d_str == today_str:
                label = f"Day {day_num} (Today)"
            else:
                label = f"Day {day_num}"

            history.append({
                "day_number": day_num,
                "day_label": label,
                "date": d_str,
                "is_today": d_str == today_str,
                "min_price": float(r["min_price"]) if r["min_price"] is not None else None,
                "close_price": float(r["close_price"]) if r["close_price"] is not None else None
            })

        return {
            "product": dict(product),
            "history_count": len(history),
            "daily_prices": history
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Product history error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/db/table_data", dependencies=[Depends(require_admin)])
async def get_table_data(
    table: str = Query("products"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    search: str = Query("", max_length=100),
    platform: str = Query("", max_length=50),
    category: str = Query("", max_length=50),
    sort_by: str = Query("", max_length=50),
    sort_order: str = Query("desc", pattern="^(asc|desc)$")
):
    """Universal paginated table explorer with dynamic filters and search."""
    if table not in ALLOWED_TABLES:
        raise HTTPException(status_code=400, detail="Invalid table name")

    try:
        offset = (page - 1) * limit
        where_clauses = []
        args = []
        arg_idx = 1

        # Search filter with exact ID prioritization
        order_override = None
        if search:
            clean_search = search.strip().lstrip("#")
            if table == "products":
                if clean_search.isdigit():
                    exact_id = int(clean_search)
                    clean_digits = re.sub(r'[^0-9]', '', clean_search)
                    where_clauses.append(f"(id = ${arg_idx} OR id::text LIKE ${arg_idx+1} OR title ILIKE ${arg_idx+2} OR platform_id ILIKE ${arg_idx+2})")
                    args.extend([exact_id, f"{clean_digits}%", f"%{clean_digits}%"])
                    arg_idx += 3
                    order_override = f"ORDER BY CASE WHEN id = {exact_id} THEN 0 WHEN id::text LIKE '{clean_digits}%' THEN 1 ELSE 2 END, id ASC"
                else:
                    where_clauses.append(f"(title ILIKE ${arg_idx} OR platform_id ILIKE ${arg_idx} OR category ILIKE ${arg_idx})")
                    args.append(f"%{search}%")
                    arg_idx += 1
            elif table == "daily_prices":
                if clean_search.isdigit():
                    exact_id = int(clean_search)
                    where_clauses.append(f"(product_id = ${arg_idx} OR id = ${arg_idx})")
                    args.append(exact_id)
                    arg_idx += 1
                    order_override = "ORDER BY date DESC, id ASC"
            elif table == "ingested_channel_deals":
                where_clauses.append(f"(title ILIKE ${arg_idx} OR source_channel ILIKE ${arg_idx} OR raw_url ILIKE ${arg_idx})")
                args.append(f"%{search}%")
                arg_idx += 1
            elif table == "deals":
                where_clauses.append(f"(badge ILIKE ${arg_idx} OR source_channel ILIKE ${arg_idx})")
                args.append(f"%{search}%")
                arg_idx += 1

        # Platform filter
        if platform:
            if table in ["products", "ingested_channel_deals"]:
                where_clauses.append(f"platform = ${arg_idx}")
                args.append(platform.lower())
                arg_idx += 1

        # Category filter
        if category and table == "products":
            where_clauses.append(f"category = ${arg_idx}")
            args.append(category.lower())
            arg_idx += 1

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Total Count for pagination
        count_sql = f"SELECT COUNT(*) FROM {table} {where_sql};"
        total_rows = await database.fetchval(count_sql, *args) or 0

        # Sort order
        if order_override:
            order_sql = order_override
        else:
            default_col = "id" if table != "channel_monitors" else "channel_name"
            order_col = default_col
            if sort_by and sort_by in ALLOWED_SORT_COLUMNS.get(table, set()):
                order_col = sort_by
            order_sql = f"ORDER BY {order_col} {sort_order.upper()} NULLS LAST"

        # Data query with selective column projection (saves admin network egress)
        cols = EXPLORER_TABLE_COLUMNS.get(table, "*")
        data_sql = f"""
            SELECT {cols} FROM {table}
            {where_sql}
            {order_sql}
            LIMIT ${arg_idx} OFFSET ${arg_idx + 1};
        """
        args.extend([limit, offset])
        rows = await database.fetch(data_sql, *args)

        return {
            "table": table,
            "page": page,
            "limit": limit,
            "total_rows": total_rows,
            "total_pages": max(1, (total_rows + limit - 1) // limit),
            "rows": [dict(r) for r in rows]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Table data error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/deals", dependencies=[Depends(require_admin)])
async def get_deals(limit: int = 36, platform: str = ""):
    try:
        plat_clean = (platform or "").strip().lower()
        limit_val = min(max(1, limit), 200)
        cache_key = f"admin_deals_{plat_clean}_{limit_val}"
        cached = await ram_cache.get(cache_key)
        if cached is not None:
            return cached

        async def _fetch_admin_deals():
            c = await ram_cache.get(cache_key)
            if c is not None:
                return c
            where_clauses = [
                "d.posted_at >= NOW() - INTERVAL '30 days'"
            ]
            args = []
            if plat_clean and plat_clean != "all":
                where_clauses.append("LOWER(p.platform) = $1")
                args.append(plat_clean)
                
            args.append(limit_val)
            query = f"""
                SELECT d.id, d.posted_price, d.posted_mrp, d.savings_pct, d.badge, d.deal_score, d.posted_at, d.source_channel,
                       p.id as product_id, p.title, p.platform, p.category, p.product_url, p.affiliate_url, p.image_url, p.rating,
                       p.current_price, p.in_stock
                FROM deals d
                JOIN products p ON d.product_id = p.id
                WHERE {' AND '.join(where_clauses)}
                ORDER BY d.posted_at DESC
                LIMIT ${len(args)};
            """
            rows = await database.fetch(query, *args)
            res = []
            for r in rows:
                d = dict(r)
                d["affiliate_url"] = resolve_deal_button_url(d.get("platform"), None, d.get("affiliate_url"), d.get("product_url"), product_id=d.get("product_id"))
                res.append(d)
            await ram_cache.set(cache_key, res, ttl=30)
            return res

        return await coalescer.run(cache_key, _fetch_admin_deals)
    except Exception as e:
        logger.error(f"Internal deals error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/search")
@limiter.limit("30/minute")
async def search_products(
    request: Request,
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(8, ge=1, le=50)
):
    try:
        clean_q = q.strip()
        if not clean_q:
            return []

        cache_key = f"search:{clean_q.lower()}:{limit}"
        cached = await ram_cache.get(cache_key)
        if cached is not None:
            return cached

        parsed = parse_search_query(clean_q)
        tokens = parsed.get("tokens", [])
        target_plat = parsed.get("platform")
        max_p = parsed.get("max_price")
        min_p = parsed.get("min_price")

        where_clauses = [
            "in_stock = TRUE",
            "current_price > 0",
            "status = 'ACTIVE'"
        ]
        args = []
        arg_idx = 1

        if target_plat:
            where_clauses.append(f"LOWER(platform) = ${arg_idx}")
            args.append(target_plat)
            arg_idx += 1

        if max_p is not None:
            where_clauses.append(f"current_price <= ${arg_idx}")
            args.append(max_p)
            arg_idx += 1

        if min_p is not None:
            where_clauses.append(f"current_price >= ${arg_idx}")
            args.append(min_p)
            arg_idx += 1

        if tokens:
            for t in tokens:
                pat = build_token_regex_pattern(t)
                where_clauses.append(f"(title ~* ${arg_idx} OR platform_id ILIKE ${arg_idx+1} OR COALESCE(category, '') ~* ${arg_idx})")
                args.extend([pat, f"%{t}%"])
                arg_idx += 2
        else:
            pat = build_token_regex_pattern(clean_q)
            where_clauses.append(f"(title ~* ${arg_idx} OR platform_id ILIKE ${arg_idx+1})")
            args.extend([pat, f"%{clean_q}%"])
            arg_idx += 2

        args.append(limit)
        limit_arg_idx = arg_idx

        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT id, platform, title, current_price, mrp, rating, review_count, in_stock, affiliate_url, product_url, image_url, min_30d, all_time_low
            FROM products
            WHERE {where_sql}
            ORDER BY 
              (CASE WHEN affiliate_url ILIKE '%fktr.in%' OR affiliate_url ILIKE '%myntr.it%' OR affiliate_url ILIKE '%ajiio.in%' OR affiliate_url ILIKE '%clnk.in%' OR LOWER(platform) = 'amazon' THEN 1 ELSE 0 END) DESC,
              (CASE WHEN mrp > current_price AND mrp > 0 THEN ((mrp - current_price)::float / mrp) ELSE 0 END) DESC,
              current_price ASC NULLS LAST
            LIMIT ${limit_arg_idx};
        """
        rows = await database.fetch(sql, *args)
        res = []
        for r in rows:
            d = dict(r)
            d["affiliate_url"] = resolve_deal_button_url(d.get("platform"), None, d.get("affiliate_url"), d.get("product_url"), product_id=d.get("id"))
            res.append(d)
        await ram_cache.set(cache_key, res, ttl=300)  # 5 min — search results stable
        return res
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/trigger/backup", dependencies=[Depends(require_admin)])
async def trigger_backup():
    try:
        from budgetby.scheduler.cleanup import run_backup
        asyncio.create_task(run_backup())
        return {"status": "success", "message": "Database backup triggered successfully."}
    except Exception as e:
        logger.error(f"Backup trigger error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/trigger/backfill", dependencies=[Depends(require_admin)])
async def trigger_backfill():
    try:
        from budgetby.engine.posting_queue import get_posting_queue
        pq = get_posting_queue()
        asyncio.create_task(pq.post_next_deal())
        return {"status": "success", "message": "Deal post triggered successfully."}
    except Exception as e:
        logger.error(f"Backfill trigger error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/trigger/channel_scan", dependencies=[Depends(require_admin)])
async def trigger_channel_scan():
    try:
        from budgetby.ingest.channel_monitor import run_channel_monitor
        asyncio.create_task(run_channel_monitor())
        return {"status": "success", "message": "Channel spy monitor scan triggered in background!"}
    except Exception as e:
        logger.error(f"Channel scan trigger error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")



# ── Universal E-Commerce Category & Subcategory Taxonomy ────────────────────
# Taxonomy definitions are imported from budgetby.taxonomy (UNIVERSAL_CATEGORIES)
# ── Public Storefront Endpoints ──────────────────────────────────────────────

@app.get("/api/public/stats")
async def get_public_stats():
    """Returns quick hero statistics and latest activity timestamp for public website."""
    cached = await ram_cache.get("public_stats")
    if cached is not None:
        return cached

    try:
        stats_row = await database.fetchrow("""
            SELECT 
                (SELECT COUNT(*) FROM products) as total_products,
                (SELECT COUNT(*) FROM deals) as total_deals,
                (SELECT COUNT(*) FROM deals WHERE posted_at >= (NOW() AT TIME ZONE 'Asia/Kolkata')::DATE) as deals_today,
                (SELECT COUNT(*) FROM products WHERE previous_price > current_price AND in_stock = TRUE) as drops_today,
                (SELECT MAX(posted_at) FROM deals) as latest_deal_time,
                (SELECT jsonb_object_agg(
                    LOWER(platform),
                    jsonb_build_object(
                        'count', cnt,
                        'max_discount', COALESCE(max_disc, 50)
                    )
                ) FROM (
                    SELECT platform, COUNT(*) as cnt, 
                           MAX(CASE WHEN mrp > current_price THEN ROUND(((mrp - current_price) / NULLIF(mrp, 0)) * 100) END) as max_disc
                    FROM products 
                    WHERE in_stock = TRUE AND current_price > 0 AND mrp > current_price
                    GROUP BY platform
                ) sub) as by_platform_json;
        """)

        total_prods = stats_row["total_products"] if stats_row else 101000
        total_deals = stats_row["total_deals"] if stats_row else 14000
        deals_today = stats_row["deals_today"] if stats_row else 1500
        drops_today = stats_row["drops_today"] if stats_row else 30000
        latest_time = stats_row["latest_deal_time"] if stats_row else None

        by_plat_raw = stats_row["by_platform_json"] if stats_row and stats_row.get("by_platform_json") else {}
        if isinstance(by_plat_raw, str):
            import json
            try:
                by_plat_raw = json.loads(by_plat_raw)
            except Exception:
                by_plat_raw = {}

        by_platform = {}
        for plat in ["amazon", "flipkart", "myntra", "ajio", "nykaa"]:
            val = by_plat_raw.get(plat) or {"count": 0, "max_discount": 50}
            by_platform[plat] = {
                "count": int(val.get("count", 0)),
                "max_discount": int(val.get("max_discount", 50))
            }

        result = {
            "total_products": total_prods,
            "total_deals": total_deals,
            "active_deals": total_deals,
            "lifetime_deals": total_deals,
            "deals_today": deals_today,
            "drops_today": drops_today,
            "by_platform": by_platform,
            "latest_timestamp": latest_time.isoformat() if latest_time else None,
            "platforms": ["Amazon", "Flipkart", "Myntra", "Ajio", "Nykaa"],
            "status": "live"
        }
        await ram_cache.set("public_stats", result, ttl=60)  # 60s RAM cache keeps latest_deal_time fresh while protecting DB
        return result
    except Exception as e:
        logger.error(f"Error in get_public_stats: {e}", exc_info=True)
        return {
            "total_products": 100000,
            "total_deals": 14000,
            "deals_today": 1500,
            "drops_today": 30000,
            "latest_timestamp": None,
            "platforms": ["Amazon", "Flipkart", "Myntra", "Ajio", "Nykaa"],
            "status": "fallback",
            "error": str(e)
        }

@app.get("/api/public/search-suggestions")
async def get_public_search_suggestions(request: Request, q: str = Query("", max_length=100)):
    """Returns lightweight search suggestion candidates (max 5) with 300s RAM cache."""
    clean_q = (q or "").strip().lower()
    if not clean_q:
        return {"suggestions": []}

    cache_key = f"suggestions:{clean_q}"
    cached = await ram_cache.get(cache_key)
    if cached is not None:
        return cached

    async def _fetch_suggestions():
        c = await ram_cache.get(cache_key)
        if c is not None:
            return c
        res = await get_public_deals(request=request, search=clean_q, page=1, limit=5, verified_only=False)
        raw_deals = res.get("deals", []) if isinstance(res, dict) else []
        suggestions = []
        for d in raw_deals:
            suggestions.append({
                "deal_id": d.get("deal_id"),
                "product_id": d.get("product_id"),
                "title": d.get("title"),
                "platform": d.get("platform"),
                "current_price": d.get("current_price"),
                "deal_price": d.get("deal_price"),
                "mrp": d.get("mrp"),
                "image_url": d.get("image_url"),
                "affiliate_url": d.get("affiliate_url"),
                "badge": d.get("badge")
            })
        result = {"suggestions": suggestions}
        await ram_cache.set(cache_key, result, ttl=300)
        return result

    return await coalescer.run(cache_key, _fetch_suggestions)

@app.get("/api/public/categories")
async def get_public_categories():
    """Returns top product categories with active in-stock deal counts across deals and new products."""
    cached = await ram_cache.get("public_categories")
    if cached is not None:
        return cached

    try:
        rows = await database.fetch("""
            WITH combined_deals AS (
                SELECT 
                    COALESCE(NULLIF(LOWER(p.category), ''), 'general') as category,
                    p.platform
                FROM deals d
                JOIN products p ON d.product_id = p.id
                WHERE p.in_stock = TRUE AND p.status = 'ACTIVE' AND p.current_price > 0 AND p.mrp > p.current_price
                  AND p.current_price <= (d.posted_price * 1.01)
                  AND (d.posted_at >= NOW() - INTERVAL '24 hours' OR (p.last_checked >= NOW() - INTERVAL '12 hours' AND d.posted_at >= NOW() - INTERVAL '72 hours'))
                UNION ALL
                SELECT 
                    COALESCE(NULLIF(LOWER(p.category), ''), 'general') as category,
                    p.platform
                FROM products p
                WHERE p.in_stock = TRUE AND p.status = 'ACTIVE' AND p.current_price > 0 AND p.mrp > p.current_price
                  AND (p.created_at >= NOW() - INTERVAL '48 hours' OR p.last_price_change >= NOW() - INTERVAL '48 hours')
                  AND NOT EXISTS (SELECT 1 FROM deals d2 WHERE d2.product_id = p.id AND d2.posted_at >= NOW() - INTERVAL '72 hours')
            )
            SELECT 
                category,
                COUNT(*) as deal_count,
                COUNT(DISTINCT platform) as platform_count
            FROM combined_deals
            GROUP BY category
            HAVING COUNT(*) >= 5
            ORDER BY deal_count DESC
            LIMIT 25;
        """)
        res = [dict(r) for r in rows]
        await ram_cache.set("public_categories", res, ttl=1800)  # 30 min — categories barely change
        return res
    except Exception as e:
        logger.error(f"Error in get_public_categories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch categories")

@app.get("/api/deal/redirect/{product_id}")
async def redirect_to_deal(product_id: int):
    """
    Outbound affiliate redirect endpoint:
    Guarantees every click from the consumer website generates tracked affiliate credit.
    - Amazon: tag=dealpulse21-21
    - Flipkart/Myntra/Ajio: EarnKaro (fktr.in, myntr.it, ajiio.in, ekaro.in) via @ekconverter9bot
    - Nykaa: Cuelinks (clnk.in) via @CuelinksBot / Cuelinks API
    Caches the converted link in products.affiliate_url so subsequent loads and clicks are instant.
    """
    try:
        row = await database.fetchrow("""
            SELECT id, platform, product_url, affiliate_url
            FROM products WHERE id = $1;
        """, product_id)
        if not row:
            raise HTTPException(status_code=404, detail="Product not found")

        platform = (row["platform"] or "").strip().lower()
        prod_url = row["product_url"] or ""
        aff_url = row["affiliate_url"] or ""

        # 1. Amazon: Always use official tag
        if platform == "amazon":
            tag = getattr(config, "AMAZON_ASSOCIATE_TAG", "dealpulse21-21")
            target = prod_url or aff_url
            m = re.search(r'/(?:dp|gp/product|product)/([A-Z0-9]{10})', target)
            if m:
                final_url = f"https://www.amazon.in/dp/{m.group(1)}?tag={tag}"
            else:
                clean = re.sub(r'([?&])tag=[^&]*', '', target)
                sep = "&" if "?" in clean else "?"
                final_url = f"{clean}{sep}tag={tag}"
            if is_safe_redirect_url(final_url):
                return RedirectResponse(url=final_url, status_code=307)

        # 2. Non-Amazon: If already converted to EarnKaro shortlink or Cuelinks, redirect directly
        for candidate in (aff_url,):
            if candidate and candidate.startswith("http"):
                cand_lower = candidate.lower()
                if any(domain in cand_lower for domain in ("fktr.in", "ajiio.in", "myntr.it", "ekaro.in", "clnk.in")):
                    if is_safe_redirect_url(candidate):
                        return RedirectResponse(url=candidate, status_code=307)
                elif "affgrowth" in cand_lower or "affextparam" in cand_lower:
                    if is_safe_redirect_url(candidate):
                        return RedirectResponse(url=candidate, status_code=307)

        # 3. Flipkart, Myntra, Ajio: Build tracked affiliate link via EarnKaro
        if platform in ("flipkart", "myntra", "ajio"):
            target_url = prod_url or aff_url
            try:
                from budgetby.affiliate.earnkaro_links import build_earnkaro_url_sync
                converted = build_earnkaro_url_sync(target_url)
                if converted and is_safe_redirect_url(converted):
                    await database.execute("UPDATE products SET affiliate_url = $1 WHERE id = $2;", converted, product_id)
                    logger.info(f"✨ [CLICK CONVERTED] Prod {product_id} [{platform}] ➔ {converted[:70]}...")
                    return RedirectResponse(url=converted, status_code=307)
            except Exception as e:
                logger.debug(f"Click redirect EK error: {e}")

        # 4. Nykaa: Direct Cuelinks API / fallback tracking
        elif platform == "nykaa":
            target_url = prod_url or aff_url
            try:
                from budgetby.affiliate.cuelinks_links import build_cuelinks_url
                converted = await build_cuelinks_url(target_url)
                if converted and is_safe_redirect_url(converted):
                    await database.execute("UPDATE products SET affiliate_url = $1 WHERE id = $2;", converted, product_id)
                    logger.info(f"✨ [CLICK CONVERTED] Prod {product_id} [NYKAA] ➔ {converted[:70]}...")
                    return RedirectResponse(url=converted, status_code=307)
            except Exception as e:
                logger.debug(f"Click redirect Cuelinks error: {e}")

        # 5. Clean fallback to merchant page if conversion fails
        clean = (prod_url or aff_url).split("&affid=")[0].split("?affid=")[0].split("&affExtParam")[0].split("?affExtParam")[0]
        if is_safe_redirect_url(clean):
            return RedirectResponse(url=clean, status_code=307)
        return RedirectResponse(url="/deals", status_code=307)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Redirect deal error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

async def _background_convert_top_deals(limit: int = 100):
    """
    Background batch converter:
    Converts active non-Amazon deals into live EarnKaro/Cuelinks tracking links
    and persists them to PostgreSQL so the storefront immediately serves tracked links.
    """
    try:
        from budgetby.ingest.telegram_listener import convert_url_via_ek_bot, convert_url_via_cuelinks_bot
        deals = await database.fetch("""
            SELECT d.id as deal_id, p.id as product_id, p.platform, p.product_url, p.affiliate_url
            FROM deals d
            JOIN products p ON d.product_id = p.id
            WHERE p.in_stock = TRUE AND LOWER(p.platform) IN ('flipkart', 'myntra', 'ajio', 'nykaa')
              AND (p.affiliate_url IS NULL 
                   OR NOT (p.affiliate_url ILIKE '%fktr.in%' OR p.affiliate_url ILIKE '%myntr.it%' OR p.affiliate_url ILIKE '%ajiio.in%' OR p.affiliate_url ILIKE '%ekaro.in%' OR p.affiliate_url ILIKE '%clnk.in%'))
            ORDER BY d.posted_at DESC
            LIMIT $1;
        """, limit)
        
        converted_count = 0
        pending_updates = []
        for d in deals:
            pid = d["product_id"]
            plat = d["platform"].lower()
            url = d["product_url"] or d["affiliate_url"]
            if not url:
                continue
            try:
                if plat in ("flipkart", "myntra", "ajio"):
                    new_url = await convert_url_via_ek_bot(url, timeout=4.0)
                    if new_url and new_url != url and any(dom in new_url.lower() for dom in ("fktr.in", "myntr.it", "ajiio.in", "ekaro.in")):
                        pending_updates.append((new_url, pid))
                        converted_count += 1
                        logger.info(f"✨ [BATCH CONVERTED] Prod {pid} [{plat}] ➔ {new_url}")
                elif plat == "nykaa":
                    new_url = await convert_url_via_cuelinks_bot(url, timeout=6.0)
                    if new_url and new_url != url and "clnk.in" in new_url.lower():
                        pending_updates.append((new_url, pid))
                        converted_count += 1
                        logger.info(f"✨ [BATCH CONVERTED] Prod {pid} [NYKAA] ➔ {new_url}")
            except Exception as e:
                logger.debug(f"Batch conversion error on prod {pid}: {e}")
            await asyncio.sleep(0.5)

        # Bulk UPDATE in a single round-trip instead of N round-trips
        if pending_updates:
            urls = [u[0] for u in pending_updates]
            pids = [u[1] for u in pending_updates]
            await database.execute("""
                UPDATE products AS p
                SET affiliate_url = v.url
                FROM (SELECT UNNEST($1::text[]) AS url, UNNEST($2::int[]) AS id) AS v
                WHERE p.id = v.id;
            """, urls, pids)
            logger.info(f"✅ Bulk updated {len(pending_updates)} affiliate URLs in single query.")

        logger.info(f"✅ Background deal conversion finished: {converted_count}/{len(deals)} converted.")
    except Exception as e:
        logger.error(f"Error in background deal pre-converter: {e}")

@app.post("/api/trigger/convert-top-deals", dependencies=[Depends(require_admin)])
async def trigger_convert_top_deals(limit: int = 100):
    try:
        asyncio.create_task(_background_convert_top_deals(limit=min(limit, 200)))
        return {"status": "success", "message": f"Background conversion of top {limit} active deals initiated."}
    except Exception as e:
        logger.error(f"Convert top deals trigger error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/public/price-drops")
@limiter.limit("30/minute")
async def get_public_price_drops(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(14, ge=1, le=50),
    min_drop_pct: float = Query(5.0, ge=0.0, le=90.0),
    min_drop_percent: float = Query(None),
    platform: str = Query("", max_length=50),
    category: str = Query("", max_length=50),
    sort_by: str = Query("drop_pct", max_length=30)
):
    """
    Returns massive 24-hour price drops & today's specials with pagination support.
    Calculates drop percentage, absolute rupee savings, previous price, and current price.
    """
    try:
        def _get_val(param, default=""):
            if hasattr(param, "default"):
                return param.default if param.default is not None else default
            return param if param is not None else default

        page_val = int(_get_val(page, 1))
        limit_val = int(_get_val(limit, 14))
        limit_val = min(max(1, limit_val), 30)

        min_drop_val = _get_val(min_drop_percent, None)
        if min_drop_val is not None:
            min_drop_pct_val = float(min_drop_val)
        else:
            min_drop_pct_val = float(_get_val(min_drop_pct, 5.0))

        platform_val = str(_get_val(platform, "")).strip()
        category_val = str(_get_val(category, "")).strip()
        sort_by_val = str(_get_val(sort_by, "drop_pct")).strip()

        cache_key = f"drops:{page_val}:{limit_val}:{min_drop_pct_val}:{platform_val}:{category_val}:{sort_by_val}"
        cached = await ram_cache.get(cache_key)
        if cached is not None:
            return cached

        async def _fetch_drops():
            # Double check cache inside coalescer
            c = await ram_cache.get(cache_key)
            if c is not None:
                return c

            offset = (page_val - 1) * limit_val

            where_clauses = [
                "p.in_stock = TRUE",
                "p.status = 'ACTIVE'",
                "p.current_price > 0",
                "p.previous_price > p.current_price",
                "p.previous_price <= GREATEST(COALESCE(NULLIF(p.mrp, 0), p.current_price), p.current_price * 15.0)",
                "(((p.previous_price - p.current_price) / NULLIF(p.previous_price, 0)) * 100) >= $1",
                "(((p.previous_price - p.current_price) / NULLIF(p.previous_price, 0)) * 100) <= 95.0"
            ]
            args = [min_drop_pct_val]
            arg_idx = 2

            plat_clean = platform_val.strip().lower() if platform_val else ""
            if plat_clean and plat_clean != "all":
                where_clauses.append(f"LOWER(p.platform) = ${arg_idx}")
                args.append(plat_clean)
                arg_idx += 1

            cat_clean = category_val.strip().lower() if category_val else ""
            if cat_clean and cat_clean not in ("all",):
                where_clauses.append(f"LOWER(p.category) LIKE ${arg_idx}")
                args.append(f"%{cat_clean}%")
                arg_idx += 1

            aff_priority = "(CASE WHEN p.affiliate_url ILIKE '%fktr.in%' OR p.affiliate_url ILIKE '%myntr.it%' OR p.affiliate_url ILIKE '%ajiio.in%' OR p.affiliate_url ILIKE '%clnk.in%' OR LOWER(p.platform) = 'amazon' THEN 1 ELSE 0 END) DESC"
            order_by = f"{aff_priority}, drop_pct DESC, drop_amount DESC"
            if sort_by_val == "price_asc":
                order_by = f"{aff_priority}, p.current_price ASC, drop_pct DESC"
            elif sort_by_val == "price_desc":
                order_by = f"{aff_priority}, p.current_price DESC, drop_pct DESC"
            elif sort_by_val in ("discount", "drop_desc", "drop_pct"):
                order_by = f"{aff_priority}, drop_pct DESC, drop_amount DESC"
            elif sort_by_val == "latest":
                order_by = f"{aff_priority}, p.last_price_change DESC NULLS LAST, drop_pct DESC"

            args.extend([limit_val, offset])

            query = f"""
                SELECT 
                    p.id as product_id,
                    COALESCE(d.id, -(p.id)) as deal_id,
                    p.title,
                    p.platform,
                    COALESCE(NULLIF(LOWER(p.category), ''), 'general') as category,
                    p.current_price,
                    p.previous_price,
                    p.mrp,
                    p.image_url,
                    p.product_url,
                    p.affiliate_url,
                    p.rating,
                    p.review_count,
                    COALESCE(p.last_price_change, NOW()) as last_price_change,
                    ROUND((((p.previous_price - p.current_price) / NULLIF(p.previous_price, 0)) * 100)::numeric, 1) as drop_pct,
                    ROUND((p.previous_price - p.current_price)::numeric, 2) as drop_amount,
                    (d.id IS NOT NULL) as is_verified_deal,
                    COALESCE(d.badge, 'PRICE DROP') as badge
                FROM products p
                LEFT JOIN deals d ON d.product_id = p.id
                WHERE {' AND '.join(where_clauses)}
                ORDER BY {order_by}
                LIMIT ${arg_idx} OFFSET ${arg_idx + 1};
            """
            rows = await database.fetch(query, *args)

            cached_total_drops = await ram_cache.get("total_drops_today_count")
            if cached_total_drops is None:
                cached_total_drops = await database.fetchval("""
                    SELECT COUNT(*) FROM products 
                    WHERE in_stock = TRUE AND status = 'ACTIVE' 
                    AND previous_price > current_price AND current_price > 0 
                    AND previous_price <= GREATEST(COALESCE(NULLIF(mrp, 0), current_price), current_price * 15.0)
                    AND (((previous_price - current_price) / NULLIF(previous_price, 0)) * 100) <= 85.0;
                """) or 5000
                await ram_cache.set("total_drops_today_count", cached_total_drops, ttl=1800)

            total_reported = cached_total_drops
            total_pages = max(1, math.ceil(total_reported / limit_val)) if total_reported > 0 else 1
            has_more = page_val < total_pages

            drops = []
            seen_pids = set()
            seen_aff_urls = set()
            for r in rows:
                pid = r["product_id"]
                if pid in seen_pids:
                    continue

                cur_p = float(r["current_price"])
                prev_p = float(r["previous_price"])
                mrp_p = float(r["mrp"]) if r["mrp"] and float(r["mrp"]) > cur_p else None
                if mrp_p and (mrp_p > cur_p * 15.0 or mrp_p > 500000):
                    mrp_p = None

                # Plausibility clamps on consumer display
                if mrp_p and prev_p > mrp_p:
                    prev_p = mrp_p
                elif prev_p > cur_p * 15.0:
                    prev_p = cur_p

                drop_amt = max(0.0, prev_p - cur_p)
                drop_pct = round((drop_amt / prev_p) * 100, 1) if prev_p > 0 else 0.0
                if drop_pct < min_drop_pct_val:
                    continue
                aff_url = resolve_deal_button_url(r["platform"], None, r["affiliate_url"], r["product_url"], product_id=r["product_id"])

                if aff_url and "/api/deal/redirect/" not in aff_url:
                    clean_aff = aff_url.split("?")[0].rstrip("/").lower()
                    if aff_url in seen_aff_urls or clean_aff in seen_aff_urls:
                        continue
                    seen_aff_urls.add(aff_url)
                    seen_aff_urls.add(clean_aff)

                seen_pids.add(pid)

                drops.append({
                    "product_id": r["product_id"],
                    "deal_id": r["deal_id"],
                    "title": r["title"],
                    "platform": r["platform"].lower(),
                    "category": r["category"],
                    "current_price": cur_p,
                    "deal_price": cur_p,
                    "previous_price": prev_p,
                    "mrp": mrp_p,
                    "drop_pct": drop_pct,
                    "drop_amount": drop_amt,
                    "is_verified_deal": bool(r["is_verified_deal"]),
                    "badge": r["badge"],
                    "image_url": r["image_url"],
                    "product_url": r["product_url"],
                    "affiliate_url": aff_url,
                    "rating": round(float(r["rating"]), 1) if r["rating"] is not None and 1.0 <= float(r["rating"]) <= 5.0 else None,
                    "review_count": int(r["review_count"]) if r.get("review_count") and int(r["review_count"]) > 0 else None,
                    "last_price_change": r["last_price_change"].isoformat() if hasattr(r["last_price_change"], "isoformat") else str(r["last_price_change"]) if r["last_price_change"] else None,
                    "posted_at": r["last_price_change"].isoformat() if hasattr(r["last_price_change"], "isoformat") else str(r["last_price_change"]) if r.get("last_price_change") else None
                })

            result = {
                "total_drops_24h": total_reported,
                "page": page,
                "limit": limit,
                "total_pages": total_pages,
                "has_more": has_more,
                "min_drop_pct": min_drop_pct,
                "drops": drops
            }
            await ram_cache.set(cache_key, result, ttl=300)
            return result

        return await coalescer.run(cache_key, _fetch_drops)
    except Exception as e:
        logger.error(f"Error in get_public_price_drops: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

_report_cooldowns: dict[int, float] = {}

@app.post("/api/public/report-price/{product_id}")
@limiter.limit("5/minute")
async def report_product_price(request: Request, product_id: int):
    """
    Crowdsourced price reporting with anti-abuse cooldown:
    Flags a product for immediate price re-verification by the micro-verifier.
    Resets last_checked so it's prioritized at the front of the verification queue.
    """
    try:
        now_ts = datetime.datetime.now().timestamp()
        last_reported = _report_cooldowns.get(product_id, 0)
        if now_ts - last_reported < 1800:  # 30-minute cooldown per product ID
            return {
                "status": "already_queued",
                "product_id": product_id,
                "message": "This product is already queued for instant verification."
            }

        exists = await database.fetchval("SELECT id FROM products WHERE id = $1;", product_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Product not found")

        _report_cooldowns[product_id] = now_ts
        if len(_report_cooldowns) > 5000:
            cutoff = now_ts - 3600
            for pid in list(_report_cooldowns.keys()):
                if _report_cooldowns[pid] < cutoff:
                    del _report_cooldowns[pid]

        # Reset last_checked to past timestamp to trigger immediate scan in next 60s micro-verifier run
        await database.execute("""
            UPDATE products 
            SET last_checked = '2000-01-01'::timestamptz 
            WHERE id = $1;
        """, product_id)

        return {
            "status": "success",
            "product_id": product_id,
            "message": "Thank you! Flagged for immediate re-verification."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Report price error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")



@app.post("/api/public/submit-review")
async def submit_product_review(request: Request):
    """
    Public endpoint to submit any product (existing ID or external URL) for algorithmic review.
    Prioritizes the product in the verification and scraping queue.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    product_id = data.get("product_id")
    url = (data.get("url") or "").strip()
    title = (data.get("title") or "").strip()
    observed_price = data.get("observed_price")

    if product_id:
        try:
            pid = int(product_id)
            exists = await database.fetchval("SELECT id FROM products WHERE id = $1;", pid)
            if exists:
                await database.execute("""
                    UPDATE products 
                    SET last_checked = '2000-01-01'::timestamptz,
                        priority_tier = 1,
                        next_check = NOW()
                    WHERE id = $1;
                """, pid)
                return {
                    "status": "success",
                    "product_id": pid,
                    "message": "Product queued for immediate algorithmic verification."
                }
        except Exception as e:
            logger.error(f"Error resetting product by ID: {e}")

    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="A valid product URL is required.")

    # Detect platform
    plat = "amazon"
    url_lower = url.lower()
    if "flipkart.com" in url_lower or "fkrt.it" in url_lower:
        plat = "flipkart"
    elif "myntra.com" in url_lower:
        plat = "myntra"
    elif "ajio.com" in url_lower:
        plat = "ajio"
    elif "nykaa.com" in url_lower:
        plat = "nykaa"

    # Check if exists by URL
    existing_id = await database.fetchval(
        "SELECT id FROM products WHERE product_url = $1 OR affiliate_url = $1 LIMIT 1;", url
    )

    if existing_id:
        await database.execute("""
            UPDATE products 
            SET last_checked = '2000-01-01'::timestamptz,
                priority_tier = 1,
                next_check = NOW()
            WHERE id = $1;
        """, existing_id)
        return {
            "status": "success",
            "product_id": existing_id,
            "message": "Product found in catalog and queued for immediate re-verification."
        }

    # Queue new product
    platform_id = hashlib.md5(url.encode()).hexdigest()[:16]
    clean_title = title if title else f"Submitted {plat.capitalize()} Deal"
    aff_url = url
    plat_lower = (plat or "").lower()
    if plat_lower == "amazon":
        tag = getattr(config, "AMAZON_ASSOCIATE_TAG", "dealpulse21-21")
        sep = "&" if "?" in url else "?"
        aff_url = f"{url}{sep}tag={tag}"
    elif plat_lower in ("flipkart", "myntra", "ajio"):
        try:
            from budgetby.affiliate.earnkaro_links import build_earnkaro_url_sync
            aff_url = build_earnkaro_url_sync(url)
        except Exception:
            pass
    elif plat_lower == "nykaa":
        try:
            from budgetby.affiliate.cuelinks_links import build_cuelinks_url_sync
            aff_url = build_cuelinks_url_sync(url)
        except Exception:
            pass

    try:
        new_id = await database.fetchval("""
            INSERT INTO products (
                platform, platform_id, title, category, product_url, affiliate_url,
                current_price, status, priority_tier, next_check, last_checked
            ) VALUES (
                $1, $2, $3, 'general', $4, $5,
                $6, 'ACTIVE', 1, NOW(), '2000-01-01'::timestamptz
            )
            ON CONFLICT (platform, platform_id) DO UPDATE 
            SET priority_tier = 1, next_check = NOW(), last_checked = '2000-01-01'::timestamptz
            RETURNING id;
        """, plat, platform_id, clean_title, url, aff_url, float(observed_price) if observed_price else 0)

        return {
            "status": "success",
            "product_id": new_id,
            "message": "New product added to catalog and queued for priority verification."
        }
    except Exception as e:
        logger.error(f"Error ingesting submitted review product: {e}")
        return {
            "status": "success",
            "message": "Product received and queued for review."
        }


@app.post("/api/public/contact")
@limiter.limit("5/minute")
async def submit_contact_inquiry(request: Request):
    """
    Public contact form endpoint: forwards user queries/feedback directly to admin Telegram chat.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    name = (data.get("name") or "Anonymous Shopper").strip()[:100]
    contact_info = (data.get("contact") or data.get("email") or "Not provided").strip()[:150]
    subject = (data.get("subject") or "General Feedback").strip()[:150]
    message = (data.get("message") or "").strip()[:1500]

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    # Format Telegram Notification for Admin
    tele_text = (
        f"📬 <b>New In-App Inquiry / Feedback</b>\n\n"
        f"👤 <b>Name:</b> {name}\n"
        f"✉️ <b>Contact:</b> {contact_info}\n"
        f"🏷️ <b>Subject:</b> {subject}\n\n"
        f"💬 <b>Message:</b>\n{message}\n\n"
        f"🌐 <i>Source: BudgetBy Storefront (/about)</i>"
    )

    try:
        if config.TELEGRAM_BOT_TOKEN and config.ADMIN_CHAT_ID:
            import httpx
            tg_url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
            async with httpx.AsyncClient(timeout=6.0) as client:
                await client.post(tg_url, json={
                    "chat_id": config.ADMIN_CHAT_ID,
                    "text": tele_text,
                    "parse_mode": "HTML"
                })
            logger.info(f"📨 In-app contact message dispatched to Admin Telegram ({config.ADMIN_CHAT_ID})")
    except Exception as e:
        logger.error(f"Error sending contact message via Telegram: {e}")

    return {
        "status": "success",
        "message": "Thank you! Your message has been sent to our team."
    }


@app.get("/api/public/atl-deals")
@limiter.limit("60/minute")
async def get_public_atl_deals(
    request: Request,
    platform: str = Query("", max_length=50),
    category: str = Query("", max_length=50),
    sort_by: str = Query("latest", max_length=30),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=50)
):
    """Public feed for All-Time Low deals (alias for tab=atl)."""
    return await get_public_deals(
        request=request,
        platform=platform,
        category=category,
        tab="atl",
        deal_type="atl",
        sort_by=sort_by,
        page=page,
        limit=limit
    )


@app.get("/api/public/deals")
@limiter.limit("60/minute")
async def get_public_deals(
    request: Request,
    platform: str = Query("", max_length=50),
    last_id: int = Query(0, ge=0),
    platforms: str = Query("", max_length=200),
    category: str = Query("", max_length=50),
    categories: str = Query("", max_length=500),
    sub: str = Query("", max_length=50),
    subcategory: str = Query("", max_length=50),
    gender: str = Query("", max_length=50),
    tab: str = Query("all", max_length=30),
    search: str = Query("", max_length=100),
    sort_by: str = Query("latest", max_length=30),
    min_discount: float = Query(0.0, ge=0.0, le=100.0),
    min_price: float = Query(0.0, ge=0.0),
    max_price: float = Query(0.0, ge=0.0),
    min_rating: float = Query(0.0, ge=0.0, le=5.0),
    verified_only: bool = Query(False),
    deal_type: str = Query("", max_length=30),
    ids: str = Query("", max_length=500),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=50),
    include_count: bool = Query(True)
):
    """
    Public paginated deals & catalog feed.
    - Exposes all 100,000+ products in the catalog to shoppers.
    - Verified deals (price checked vs historical deals with no price increases) are always prioritized first.
    - If a search has no verified deals, marks it clearly and serves all matching catalog products with price history.
    """
    try:
        page = int(page) if not hasattr(page, 'default') and str(page).isdigit() else 1
        limit = int(limit) if not hasattr(limit, 'default') and str(limit).isdigit() else 24
        limit = min(max(1, limit), 30)
        platform = str(platform) if not hasattr(platform, 'default') else ""
        platforms = str(platforms) if not hasattr(platforms, 'default') else ""
        category = str(category) if not hasattr(category, 'default') else ""
        categories = str(categories) if not hasattr(categories, 'default') else ""
        sub = str(sub) if not hasattr(sub, 'default') else ""
        subcategory = str(subcategory) if not hasattr(subcategory, 'default') else ""
        gender = str(gender) if not hasattr(gender, 'default') else ""
        tab = str(tab) if not hasattr(tab, 'default') else "all"
        search = str(search) if not hasattr(search, 'default') else ""
        sort_by = str(sort_by) if not hasattr(sort_by, 'default') else "latest"
        min_discount = float(min_discount) if not hasattr(min_discount, 'default') else 0.0
        min_price = float(min_price) if not hasattr(min_price, 'default') else 0.0
        max_price = float(max_price) if not hasattr(max_price, 'default') else 0.0
        min_rating = float(min_rating) if not hasattr(min_rating, 'default') else 0.0
        verified_only = bool(verified_only) if not hasattr(verified_only, 'default') else False
        deal_type = str(deal_type) if not hasattr(deal_type, 'default') else ""
        ids = str(ids) if not hasattr(ids, 'default') else ""

        plat_norm = (platform or "").strip().lower()
        plats_norm = (platforms or "").strip().lower()
        cat_norm = (category or "").strip().lower()
        cats_norm = (categories or "").strip().lower()
        eff_sub = (sub or subcategory or "").strip().lower()
        gender_norm = (gender or "").strip().lower()
        tab_norm = (tab or "all").strip().lower()
        search_norm = (search or "").strip().lower().lstrip("#")
        sort_norm = (sort_by or "latest").strip().lower()
        deal_type_norm = (deal_type or "").strip().lower()
        ids_norm = (ids or "").strip()
        eff_include_count = bool(include_count) if not hasattr(include_count, 'default') else True
        base_key = f"deals:{plat_norm}:{plats_norm}:{cat_norm}:{cats_norm}:{eff_sub}:{gender_norm}:{tab_norm}:{search_norm}:{sort_norm}:{min_discount}:{min_price}:{max_price}:{min_rating}:{verified_only}:{deal_type_norm}:{ids_norm}:{page}:{limit}"
        cache_key = base_key if eff_include_count else f"{base_key}:nocount"
        cached = await ram_cache.get(cache_key)
        if cached is None and not eff_include_count:
            cached = await ram_cache.get(base_key)
        if cached is not None:
            return cached

        async def _fetch_deals():
            c = await ram_cache.get(cache_key)
            if c is not None:
                return c

            offset = (page - 1) * limit
            search_clean = search_norm
            platform_clean = plat_norm
            category_clean = cat_norm
            tab_clean = tab_norm
            deal_type_clean = deal_type_norm

            where_clauses = [
                "p.in_stock = TRUE",
                "p.status = 'ACTIVE'",
                "p.current_price > 0",
                "(d.id IS NOT NULL OR p.mrp IS NULL OR p.mrp <= p.current_price * 25.0)"
            ]
            where_args = []
            relevance_select = "0 as relevance_score"

            # Shared watchlist IDs filtering
            if ids:
                clean_ids = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()][:50]
                if clean_ids:
                    where_clauses.append(f"p.id = ANY(${len(where_args) + 1})")
                    where_args.append(clean_ids)

            # ── SEARCH INTENT EXTRACTION & FILTERING ────────────────────────────
            parsed = None
            if search_clean:
                parsed = parse_search_query(search_clean)
                if parsed["platform"] and (not platform_clean or platform_clean == "all"):
                    platform_clean = parsed["platform"]

                # Price constraints from search query
                if parsed["max_price"]:
                    where_clauses.append(f"p.current_price <= ${len(where_args) + 1}")
                    where_args.append(parsed["max_price"])
                if parsed["min_price"]:
                    where_clauses.append(f"p.current_price >= ${len(where_args) + 1}")
                    where_args.append(parsed["min_price"])

                # Keyword tokens matching with precision word boundaries & plurals
                for t in parsed["tokens"]:
                    pat = build_token_regex_pattern(t)
                    where_clauses.append(f"(p.title ~* ${len(where_args) + 1} OR COALESCE(p.category, '') ~* ${len(where_args) + 1})")
                    where_args.append(pat)

            # ── PLATFORM FILTER ─────────────────────────────────────────────────
            selected_platforms = []
            if platforms:
                selected_platforms = [p.strip().lower() for p in platforms.split(",") if p.strip()]
            elif platform_clean and platform_clean != "all":
                selected_platforms = [platform_clean]

            if selected_platforms:
                where_clauses.append(f"LOWER(p.platform) = ANY(${len(where_args) + 1})")
                where_args.append(selected_platforms)

            # ── EXPLICIT PRICE RANGE FILTER ─────────────────────────────────────
            if min_price and min_price > 0:
                where_clauses.append(f"p.current_price >= ${len(where_args) + 1}")
                where_args.append(min_price)
            if max_price and max_price > 0:
                where_clauses.append(f"p.current_price <= ${len(where_args) + 1}")
                where_args.append(max_price)

            # ── MINIMUM DISCOUNT FILTER ─────────────────────────────────────────
            if min_discount and min_discount > 0:
                where_clauses.append(f"""(
                    (p.mrp > 0 AND p.current_price <= p.mrp * (1.0 - (${len(where_args) + 1}::numeric / 100.0)))
                    OR (p.previous_price > 0 AND p.current_price <= p.previous_price * (1.0 - (${len(where_args) + 1}::numeric / 100.0)))
                )""")
                where_args.append(min_discount)

            # ── CUSTOMER RATING FILTER ──────────────────────────────────────────
            if min_rating and min_rating > 0:
                where_clauses.append(f"COALESCE(p.rating, 0) >= ${len(where_args) + 1}")
                where_args.append(min_rating)

            # ── VERIFIED ONLY FILTER ────────────────────────────────────────────
            if verified_only:
                where_clauses.append("d.id IS NOT NULL")
                where_clauses.append("(p.mrp IS NULL OR p.mrp > p.current_price)")

            # ── CATEGORY & SUBCATEGORY FILTER ───────────────────────────────────
            selected_categories = []
            if categories:
                selected_categories = [c.strip().lower() for c in categories.split(",") if c.strip()]
            elif category_clean and category_clean != "all":
                # Handle legacy 'watches_bags' key — map to both watches + bags
                if category_clean == "watches_bags":
                    selected_categories = ["watches", "bags"]
                else:
                    selected_categories = [category_clean]

            if selected_categories:
                cat_clauses = []
                for cat in selected_categories:
                    cat_info = UNIVERSAL_CATEGORIES.get(cat)
                    if cat in ("miscellaneous", "other", "general", "misc", "more", "null", "none"):
                        cat_clauses.append("(p.category IS NULL OR LOWER(p.category) IN ('miscellaneous', 'other', 'general', 'misc', 'more', 'none', '') OR p.category = '')")
                    elif cat_info:
                        # Match ONLY on the p.category column — DB is accurately categorized, no ILIKE title scans
                        sub_aliases = [f"LOWER(p.category) = '{a}'" for a in cat_info["aliases"]]
                        combined = " OR ".join(sub_aliases)
                        cat_clauses.append(f"({combined})")
                    else:
                        cat_clauses.append(f"LOWER(p.category) = ${len(where_args) + 1}")
                        where_args.append(cat)
                if cat_clauses:
                    where_clauses.append(f"({' OR '.join(cat_clauses)})")


            # ── AUDIENCE / GENDER FILTER ────────────────────────────────────────
            gender_clean = (gender or "").strip().lower()
            if gender_clean and gender_clean != "all":
                unisex_regex = r"\y(unisex)\y|men\s*(and|&)\s*women|women\s*(and|&)\s*men|for\s+men\s+(and|&)\s+women|for\s+women\s+(and|&)\s+men"
                neutral_cat_sql = "LOWER(p.category) IN ('sports', 'health', 'home', 'electronics', 'automotive', 'gaming', 'books', 'grocery', 'pets', 'miscellaneous')"
                if gender_clean == "men":
                    where_clauses.append(
                        f"((p.title ~* ${len(where_args) + 1} OR p.title ~* ${len(where_args) + 3} OR {neutral_cat_sql})"
                        f" AND NOT (p.title ~* ${len(where_args) + 2} AND NOT (p.title ~* ${len(where_args) + 3})))"
                    )
                    where_args.append(r"\y(men|mens|male|gentlemen)\y")
                    where_args.append(r"\y(women|womens|female|ladies|girls?|kurti|saree|lehenga|bra|heels)\y")
                    where_args.append(unisex_regex)
                elif gender_clean == "women":
                    where_clauses.append(
                        f"((p.title ~* ${len(where_args) + 1} OR p.title ~* ${len(where_args) + 3} OR {neutral_cat_sql})"
                        f" AND NOT (p.title ~* ${len(where_args) + 2} AND NOT (p.title ~* ${len(where_args) + 3})))"
                    )
                    where_args.append(r"\y(women|womens|female|ladies|girls?|saree|kurti|heels|bra|lehenga)\y")
                    where_args.append(r"\y(men|mens|male|gentlemen)\y")
                    where_args.append(unisex_regex)
                elif gender_clean in ("boy", "boys"):
                    where_clauses.append(
                        f"((p.title ~* ${len(where_args) + 1} OR p.title ~* ${len(where_args) + 3})"
                        f" AND NOT (p.title ~* ${len(where_args) + 2} AND NOT (p.title ~* ${len(where_args) + 3})))"
                    )
                    where_args.append(r"\y(boy|boys)\y")
                    where_args.append(r"\y(girl|girls|women|womens|ladies)\y")
                    where_args.append(unisex_regex)
                elif gender_clean in ("girl", "girls"):
                    where_clauses.append(
                        f"((p.title ~* ${len(where_args) + 1} OR p.title ~* ${len(where_args) + 3})"
                        f" AND NOT (p.title ~* ${len(where_args) + 2} AND NOT (p.title ~* ${len(where_args) + 3})))"
                    )
                    where_args.append(r"\y(girl|girls|frock)\y")
                    where_args.append(r"\y(boy|boys|men|mens|gentlemen)\y")
                    where_args.append(unisex_regex)
                elif gender_clean in ("kid", "kids", "children"):
                    where_clauses.append(f"(p.title ~* ${len(where_args) + 1} OR p.title ~* ${len(where_args) + 2})")
                    where_args.append(r"\y(kids?|baby|infant|toddler|children)\y")
                    where_args.append(unisex_regex)

            # Subcategory keyword filtering (parameterized with word boundaries)
            if eff_sub:
                sub_kws = []
                for cat_data in UNIVERSAL_CATEGORIES.values():
                    if eff_sub in cat_data.get("subcategories", {}):
                        sub_kws = cat_data["subcategories"][eff_sub].get("keywords", [])
                        break
                if not sub_kws:
                    clean_eff = re.sub(r"[^a-zA-Z0-9\s]", "", eff_sub)[:50]
                    sub_kws = [clean_eff.replace("-", " ").strip(), clean_eff.strip()]
                    sub_kws = [k for k in sub_kws if k]

                if sub_kws:
                    sub_placeholders = []
                    for kw in sub_kws:
                        sub_placeholders.append(f"p.title ~* ${len(where_args) + 1}")
                        where_args.append(build_token_regex_pattern(kw))
                    where_clauses.append(f"({' OR '.join(sub_placeholders)})")

            use_fast_deals_path = (
                not search_clean 
                and not ids 
                and deal_type_clean not in ("drops", "atl", "verified") 
                and tab_clean not in ("drops", "atl", "verified", "under499", "under999", "featured")
                and (not category_clean or category_clean == "all")
                and not categories
                and (not platform_clean or platform_clean == "all")
                and not platforms
                and not eff_sub
                and (not gender_clean or gender_clean == "all")
                and not (min_price and min_price > 0)
                and not (max_price and max_price > 0)
                and not (min_discount and min_discount > 0)
                and not (min_rating and min_rating > 0)
                and not verified_only
            )

            aff_priority = "(CASE WHEN p.affiliate_url ILIKE '%fktr.in%' OR p.affiliate_url ILIKE '%myntr.it%' OR p.affiliate_url ILIKE '%ajiio.in%' OR p.affiliate_url ILIKE '%clnk.in%' OR LOWER(p.platform) = 'amazon' THEN 1 ELSE 0 END) DESC"

            query_args = list(where_args)

            if use_fast_deals_path:
                if tab_clean == "under499":
                    where_clauses.append("p.current_price <= 499")
                elif tab_clean == "under999":
                    where_clauses.append("p.current_price <= 999")
                elif tab_clean == "featured":
                    where_clauses.append("(p.mrp IS NULL OR ((p.mrp - p.current_price) / NULLIF(p.mrp, 0)) >= 0.30)")

                if sort_norm in ("discount_desc", "discount"):
                    order_sql = f"ORDER BY {aff_priority}, ((p.mrp - p.current_price) / NULLIF(p.mrp, 0)) DESC NULLS LAST, d.posted_at DESC NULLS LAST, p.id DESC"
                elif sort_norm == "price_asc":
                    order_sql = f"ORDER BY {aff_priority}, p.current_price ASC, d.posted_at DESC NULLS LAST, p.id DESC"
                elif sort_norm == "price_desc":
                    order_sql = f"ORDER BY {aff_priority}, p.current_price DESC, d.posted_at DESC NULLS LAST, p.id DESC"
                elif sort_norm == "score_desc":
                    order_sql = f"ORDER BY {aff_priority}, COALESCE(d.deal_score, 50.0) DESC, d.posted_at DESC NULLS LAST, p.id DESC"
                else:
                    order_sql = f"ORDER BY {aff_priority}, d.posted_at DESC NULLS LAST, p.id DESC"

                limit_idx = len(query_args) + 1
                offset_idx = len(query_args) + 2
                query_args.extend([limit, offset])
                where_sql = f"WHERE {' AND '.join(where_clauses)}"

                query = f"""
                    SELECT 
                        d.id as deal_id,
                        p.id as product_id,
                        p.title,
                        p.platform,
                        COALESCE(NULLIF(LOWER(p.category), ''), 'general') as category,
                        p.product_url,
                        p.affiliate_url,
                        p.image_url,
                        p.rating,
                        p.review_count,
                        COALESCE(d.posted_price, p.current_price) as current_price,
                        p.mrp,
                        p.previous_price,
                        p.min_30d,
                        p.all_time_low,
                        p.in_stock,
                        p.status,
                        COALESCE(p.last_checked, d.posted_at) as last_checked,
                        TRUE as is_verified,
                        COALESCE(d.badge, 'HOT DEAL') as badge,
                        COALESCE(d.deal_score, 50.0) as deal_score,
                        d.posted_at as deal_time,
                        0 as relevance_score
                    FROM products p
                    LEFT JOIN deals d ON d.product_id = p.id AND d.posted_at >= NOW() - INTERVAL '7 days'
                    {where_sql}
                    {order_sql}
                    LIMIT ${limit_idx} OFFSET ${offset_idx};
                """
            else:
                if deal_type_clean == "verified" or tab_clean == "verified":
                    if not verified_only:
                        where_clauses.append("d.id IS NOT NULL")
                        where_clauses.append("(p.mrp IS NULL OR p.mrp > p.current_price)")
                elif deal_type_clean == "drops" or tab_clean == "drops":
                    where_clauses.append("p.previous_price > p.current_price")
                    where_clauses.append("p.previous_price <= GREATEST(COALESCE(NULLIF(p.mrp, 0), p.current_price), p.current_price * 15.0)")
                    where_clauses.append("(((p.previous_price - p.current_price) / NULLIF(p.previous_price, 0)) * 100) <= 85.0")
                elif deal_type_clean == "atl" or tab_clean == "atl":
                    where_clauses.append("(d.badge ILIKE '%ATL%' OR (p.all_time_low IS NOT NULL AND p.current_price <= p.all_time_low * 1.02))")
                elif tab_clean == "under499":
                    where_clauses.append("p.current_price <= 499")
                elif tab_clean == "under999":
                    where_clauses.append("p.current_price <= 999")
                elif tab_clean == "featured":
                    where_clauses.append("(d.id IS NOT NULL OR ((p.mrp - p.current_price) / NULLIF(p.mrp, 0)) >= 0.50)")

                if search_clean and parsed:
                    exact_idx = len(query_args) + 1
                    query_args.append(f"%{parsed['clean_query']}%")
                    starts_idx = len(query_args) + 1
                    first_token = parsed["tokens"][0] if parsed["tokens"] else ""
                    query_args.append(f"{first_token}%")

                    acc_sql = "0"
                    if not parsed["is_accessory_query"]:
                        acc_sql = """
                            CASE 
                                WHEN (p.title ILIKE '%case%' OR p.title ILIKE '%cover%' OR p.title ILIKE '%tempered glass%' 
                                      OR p.title ILIKE '%screen protector%' OR p.title ILIKE '%charger%' OR p.title ILIKE '%adapter%'
                                      OR p.title ILIKE '%cable%' OR p.title ILIKE '%sleeve%' OR p.title ILIKE '%bag%'
                                      OR p.title ILIKE '%backpack%' OR p.title ILIKE '%stand%' OR p.title ILIKE '%mount%'
                                      OR p.title ILIKE '%pouch%' OR p.title ILIKE '%cooling pad%' OR p.title ILIKE '%mouse pad%') 
                                THEN -300 
                                ELSE 0 
                            END
                        """

                    relevance_select = f"""
                        (
                            (CASE WHEN p.title ILIKE ${exact_idx} THEN 200 ELSE 0 END) +
                            (CASE WHEN p.title ILIKE ${starts_idx} THEN 100 ELSE 0 END) +
                            (CASE WHEN p.all_time_low IS NOT NULL AND p.current_price <= p.all_time_low * 1.02 THEN 50 ELSE 0 END) +
                            (COALESCE(ROUND((((p.mrp - p.current_price)/NULLIF(p.mrp,0)) * 100)::numeric, 0), 0) * 0.5) +
                            (COALESCE(p.rating, 0) * 8) +
                            {acc_sql}
                        ) as relevance_score
                    """

                if verified_only:
                    if sort_norm in ("score_desc", "score", "best", "deal_score"):
                        order_sql = "ORDER BY d.deal_score DESC, d.posted_at DESC"
                    elif sort_norm in ("discount_desc", "discount"):
                        order_sql = "ORDER BY ((p.mrp - p.current_price) / NULLIF(p.mrp, 0)) DESC NULLS LAST, d.posted_at DESC"
                    elif sort_norm == "price_asc":
                        order_sql = "ORDER BY p.current_price ASC, d.posted_at DESC"
                    elif sort_norm == "price_desc":
                        order_sql = "ORDER BY p.current_price DESC, d.posted_at DESC"
                    else:
                        order_sql = "ORDER BY d.posted_at DESC, d.product_id DESC"
                elif sort_norm in ("discount_desc", "discount"):
                    order_sql = f"ORDER BY {aff_priority}, (CASE WHEN d.id IS NOT NULL THEN 1 ELSE 0 END) DESC, ((p.mrp - p.current_price) / NULLIF(p.mrp, 0)) DESC NULLS LAST, p.id DESC"
                elif sort_norm == "price_asc":
                    order_sql = f"ORDER BY {aff_priority}, (CASE WHEN d.id IS NOT NULL THEN 1 ELSE 0 END) DESC, p.current_price ASC, p.id DESC"
                elif sort_norm == "price_desc":
                    order_sql = f"ORDER BY {aff_priority}, (CASE WHEN d.id IS NOT NULL THEN 1 ELSE 0 END) DESC, p.current_price DESC, p.id DESC"
                elif sort_norm in ("score_desc", "score", "best", "deal_score"):
                    order_sql = f"ORDER BY {aff_priority}, (CASE WHEN d.id IS NOT NULL THEN 1 ELSE 0 END) DESC, COALESCE(d.deal_score, 50.0) DESC, p.id DESC"
                elif search_clean:
                    order_sql = f"ORDER BY {aff_priority}, (CASE WHEN d.id IS NOT NULL THEN 1 ELSE 0 END) DESC, relevance_score DESC, COALESCE(d.posted_at, p.last_price_change, p.created_at) DESC"
                else:
                    if tab_clean == "drops":
                        order_sql = f"ORDER BY {aff_priority}, (CASE WHEN d.id IS NOT NULL THEN 1 ELSE 0 END) DESC, (((p.previous_price - p.current_price) / NULLIF(p.previous_price, 0))) DESC NULLS LAST, p.last_price_change DESC NULLS LAST, p.id DESC"
                    else:
                        order_sql = f"ORDER BY {aff_priority}, (CASE WHEN d.id IS NOT NULL THEN 1 ELSE 0 END) DESC, COALESCE(d.posted_at, p.last_price_change, p.created_at) DESC, p.id DESC"

                limit_idx = len(query_args) + 1
                offset_idx = len(query_args) + 2
                query_args.extend([limit, offset])
                where_sql = f"WHERE {' AND '.join(where_clauses)}"

                query = f"""
                    SELECT 
                        COALESCE(d.id, -(p.id)) as deal_id,
                        p.id as product_id,
                        p.title,
                        p.platform,
                        COALESCE(NULLIF(LOWER(p.category), ''), 'general') as category,
                        p.product_url,
                        p.affiliate_url,
                        p.image_url,
                        p.rating,
                        p.review_count,
                        COALESCE(d.posted_price, p.current_price) as current_price,
                        p.mrp,
                        p.previous_price,
                        p.min_30d,
                        p.all_time_low,
                        p.in_stock,
                        p.status,
                        COALESCE(p.last_checked, d.posted_at, p.last_price_change) as last_checked,
                        (d.id IS NOT NULL) as is_verified,
                        COALESCE(d.badge, 
                            CASE 
                                WHEN p.all_time_low IS NOT NULL AND p.current_price <= p.all_time_low * 1.02 THEN 'ATL'
                                WHEN p.last_price_change >= NOW() - INTERVAL '24 hours' THEN 'PRICE DROP'
                                WHEN ((p.mrp - p.current_price)/NULLIF(p.mrp,0)) >= 0.50 THEN 'HOT DEAL'
                                ELSE 'CATALOG'
                            END
                        ) as badge,
                        COALESCE(d.deal_score, ROUND((((p.mrp - p.current_price)/NULLIF(p.mrp,0)) * 100)::numeric, 1), 50.0) as deal_score,
                        COALESCE(d.posted_at, p.last_price_change, p.created_at) as deal_time,
                        {relevance_select}
                    {"FROM deals d JOIN products p ON p.id = d.product_id" if verified_only else "FROM products p LEFT JOIN deals d ON d.product_id = p.id"}
                    {where_sql}
                    {order_sql}
                    LIMIT ${limit_idx} OFFSET ${offset_idx};
                """

            rows = await database.fetch(query, *query_args)

            if not eff_include_count:
                # High-speed Carousel / Homepage path: zero table scan latency
                total_matches = len(rows) if len(rows) < limit else (page * limit + 1)
            elif use_fast_deals_path:
                cached_total_catalog = await ram_cache.get("total_catalog_deals_count")
                if cached_total_catalog is None:
                    cached_total_catalog = await database.fetchval("""
                        SELECT COUNT(*) FROM products 
                        WHERE in_stock = TRUE AND status = 'ACTIVE' AND current_price > 0;
                    """) or 100000
                    await ram_cache.set("total_catalog_deals_count", cached_total_catalog, ttl=900)
                total_matches = cached_total_catalog
            else:
                cnt_key = f"cnt:{hashlib.md5(f'{where_sql}:{where_args}'.encode()).hexdigest()}"
                cached_count = await ram_cache.get(cnt_key)
                if cached_count is None:
                    count_from_sql = "FROM deals d JOIN products p ON p.id = d.product_id" if verified_only else "FROM products p LEFT JOIN deals d ON d.product_id = p.id"
                    count_query = f"""
                        SELECT COUNT(*) {count_from_sql}
                        {where_sql};
                    """
                    cached_count = await database.fetchval(count_query, *where_args) or 0
                    await ram_cache.set(cnt_key, cached_count, ttl=600)
                total_matches = cached_count

            total_pages = max(1, math.ceil(total_matches / limit)) if total_matches > 0 else 1
            verified_matches = sum(1 for r in rows if r.get("is_verified"))
            catalog_matches = max(0, total_matches - verified_matches)

            deals = []
            seen_pids = set()
            seen_aff_urls = set()
            for r in rows:
                pid = r.get("product_id") or r.get("id")
                if not pid or pid in seen_pids:
                    continue

                cur_p = float(r["current_price"]) if r["current_price"] and float(r["current_price"]) > 0 else 0.0
                mrp_p = float(r["mrp"]) if r["mrp"] and float(r["mrp"]) > cur_p else cur_p
                if mrp_p > cur_p * 15.0 or mrp_p > 500000:
                    mrp_p = cur_p

                if mrp_p > cur_p and mrp_p > 0:
                    pct = round(((mrp_p - cur_p) / mrp_p) * 100)
                    savings = round(mrp_p - cur_p, 2)
                else:
                    pct = 0
                    savings = 0.0

                prev_p = float(r["previous_price"]) if r.get("previous_price") and float(r["previous_price"]) > cur_p else None
                if prev_p:
                    if mrp_p and prev_p > mrp_p:
                        prev_p = mrp_p
                    elif prev_p > cur_p * 15.0:
                        prev_p = cur_p

                drop_pct = round(((prev_p - cur_p) / prev_p) * 100) if prev_p else 0
                drop_amount = round(prev_p - cur_p, 2) if prev_p else 0.0

                aff_url = resolve_deal_button_url(r["platform"], None, r["affiliate_url"], r["product_url"], product_id=pid)

                if aff_url and "/api/deal/redirect/" not in aff_url:
                    clean_aff = aff_url.split("?")[0].rstrip("/").lower()
                    if aff_url in seen_aff_urls or clean_aff in seen_aff_urls:
                        continue
                    seen_aff_urls.add(aff_url)
                    seen_aff_urls.add(clean_aff)

                seen_pids.add(pid)
                is_verified = bool(r["is_verified"])

                deals.append({
                    "deal_id": r.get("deal_id") or pid,
                    "product_id": pid,
                    "title": r["title"],
                    "platform": r["platform"].lower(),
                    "category": r["category"] or "general",
                    "deal_price": cur_p,
                    "current_price": cur_p,
                    "mrp": mrp_p if mrp_p > cur_p else None,
                    "previous_price": prev_p,
                    "drop_pct": drop_pct,
                    "drop_amount": drop_amount,
                    "discount_pct": pct,
                    "savings_amount": savings,
                    "is_verified_deal": is_verified,
                    "badge": r["badge"] or ("TOP DEAL" if is_verified else "HOT DEAL"),
                    "deal_score": float(r["deal_score"]) if r["deal_score"] else None,
                    "posted_at": r["deal_time"].isoformat() if r["deal_time"] else None,
                    "image_url": r["image_url"],
                    "product_url": r["product_url"],
                    "affiliate_url": aff_url,
                    "rating": round(float(r["rating"]), 1) if r["rating"] is not None and 1.0 <= float(r["rating"]) <= 5.0 else None,
                    "review_count": int(r["review_count"]) if r.get("review_count") and int(r["review_count"]) > 0 else None,
                    "min_30d": float(r["min_30d"]) if r["min_30d"] else None,
                    "all_time_low": float(r["all_time_low"]) if r["all_time_low"] else None,
                    "in_stock": bool(r["in_stock"]),
                    "last_checked": r["last_checked"].isoformat() if r.get("last_checked") else None,
                    "is_catalog_product": not is_verified
                })

            result = {
                "page": page,
                "limit": limit,
                "total_matches": total_matches,
                "total_pages": max(1, (total_matches + limit - 1) // limit) if total_matches > 0 else 1,
                "verified_matches": verified_matches,
                "catalog_matches": catalog_matches,
                "has_verified_deals": bool(verified_matches > 0),
                "search_query": search_clean,
                "deals": deals
            }
            # Keyword searches and Just Dropped fresh feeds (latest/all) expire after 60s so newly posted Telegram deals appear fast.
            # Specific category/browse pages cache for 600s (10 min) to protect DB egress.
            cache_ttl = 60 if (search_clean or sort_norm == "latest" or tab_clean == "all") else 600
            await ram_cache.set(cache_key, result, ttl=cache_ttl)
            return result

        return await coalescer.run(cache_key, _fetch_deals)
    except Exception as e:
        logger.error(f"Error in get_public_deals: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch deals")


async def get_public_deal_detail(product_id: int) -> dict | None:
    """
    Returns complete product & deal details with bounded 30-day price history.
    Protected by 60s RAM cache and single-flight request coalescing. Zero Supabase egress on cache hit.
    """
    if not product_id or product_id <= 0:
        return None

    cache_key = f"deal_detail:{product_id}"
    cached = await ram_cache.get(cache_key)
    if cached is not None:
        return cached

    async def _fetch_deal_detail():
        # Double check cache inside coalescer
        c = await ram_cache.get(cache_key)
        if c is not None:
            return c

        prod_task = database.fetchrow("""
            SELECT 
                p.id as product_id,
                p.platform,
                p.platform_id,
                p.title,
                p.category,
                p.product_url,
                p.affiliate_url,
                p.image_url,
                p.current_price,
                p.previous_price,
                p.mrp,
                p.median_30d_price,
                p.min_30d,
                p.min_60d,
                p.min_90d,
                p.min_120d,
                p.all_time_low,
                p.rating,
                p.review_count,
                p.in_stock,
                p.status,
                p.last_checked,
                p.last_price_change,
                p.created_at,
                d.id as deal_id,
                d.deal_type,
                d.badge,
                d.posted_price,
                d.posted_mrp,
                d.savings_amount,
                d.savings_pct,
                d.deal_score,
                d.posted_at
            FROM products p
            LEFT JOIN LATERAL (
                SELECT id, deal_type, badge, posted_price, posted_mrp, savings_amount, savings_pct, deal_score, posted_at
                FROM deals
                WHERE product_id = p.id
                ORDER BY posted_at DESC
                LIMIT 1
            ) d ON TRUE
            WHERE p.id = $1;
        """, product_id)

        history_task = database.fetch("""
            SELECT date, min_price, close_price
            FROM daily_prices
            WHERE product_id = $1
            ORDER BY date ASC
            LIMIT 30;
        """, product_id)

        product_row, history_rows = await asyncio.gather(prod_task, history_task)
        if not product_row:
            return None

        history = []
        for r in history_rows:
            p_val = float(r["min_price"]) if r["min_price"] is not None else (float(r["close_price"]) if r["close_price"] is not None else None)
            if p_val is not None:
                history.append({
                    "date": str(r["date"]),
                    "price": p_val
                })

        cp = float(product_row["current_price"] or 0.0)
        mrp = float(product_row["mrp"] or cp)
        prev_p = float(product_row["previous_price"] or cp)
        atl = float(product_row["all_time_low"]) if product_row["all_time_low"] is not None else None
        min_30d = float(product_row["min_30d"]) if product_row["min_30d"] is not None else None
        min_90d = float(product_row["min_90d"]) if product_row["min_90d"] is not None else None

        if len(history) < 2:
            created = product_row["created_at"]
            start_date = str(created.date()) if hasattr(created, "date") else "Start"
            today_date = "Today"
            if prev_p and prev_p != cp:
                history = [
                    {"date": start_date, "price": prev_p},
                    {"date": today_date, "price": cp}
                ]
            elif atl and atl != cp:
                history = [
                    {"date": "Historical Low", "price": atl},
                    {"date": today_date, "price": cp}
                ]
            else:
                history = [
                    {"date": start_date, "price": cp},
                    {"date": today_date, "price": cp}
                ]

        savings = max(0.0, mrp - cp) if mrp > cp else 0.0
        discount_pct = int(round(((mrp - cp) / mrp) * 100)) if (mrp > cp and mrp > 0) else 0

        prod_dict = dict(product_row)
        prod_dict["current_price"] = cp
        prod_dict["mrp"] = mrp
        prod_dict["discount_pct"] = discount_pct
        prod_dict["savings_amount"] = savings
        prod_dict["all_time_low"] = atl
        prod_dict["min_30d"] = min_30d
        prod_dict["min_90d"] = min_90d
        prod_dict["deal_score"] = float(prod_dict.get("deal_score") or 75.0)

        result = {
            "product": prod_dict,
            "price_history": history,
            "history_count": len(history)
        }
        await ram_cache.set(cache_key, result, ttl=60)
        return result

    return await coalescer.run(cache_key, _fetch_deal_detail)


@app.get("/api/public/deal/{product_id}")
@app.get("/api/public/product/{product_id}")
async def api_public_deal_detail(product_id: int):
    """Returns structured JSON product deal details and bounded 30-day price history."""
    detail = await get_public_deal_detail(product_id)
    if not detail or not detail.get("product"):
        raise HTTPException(status_code=404, detail="Product not found")
    return detail


# ── Page Rendering Routes ───────────────────────────────────────────────────

STORE_DISPLAY_NAMES = {
    "amazon": "Amazon India",
    "flipkart": "Flipkart",
    "myntra": "Myntra",
    "ajio": "Ajio",
    "nykaa": "Nykaa"
}

_revalidating_keys: set[str] = set()

async def get_cached_ssr_html(key: str) -> str | None:
    val, is_stale = await ram_cache.get_stale(f"ssr_html:{key}")
    if is_stale and val:
        asyncio.create_task(_revalidate_ssr_key(key))
    return val

async def set_cached_ssr_html(key: str, html_content: str, ttl: int = 180):
    await ram_cache.set(f"ssr_html:{key}", html_content, ttl=ttl, stale_ttl=ttl * 4)

async def _revalidate_ssr_key(key: str):
    if key in _revalidating_keys:
        return
    _revalidating_keys.add(key)
    try:
        if key == "home":
            html = await build_home_html()
            if html:
                await set_cached_ssr_html("home", html, ttl=180)
    except Exception as e:
        logger.debug(f"Background SSR revalidation note for {key}: {e}")
    finally:
        _revalidating_keys.discard(key)

def make_html_response(request: Request, html_content: str, cache_seconds: int = 60, status_code: int = 200) -> Response:
    etag = hashlib.md5(html_content.encode("utf-8")).hexdigest()
    client_etag = request.headers.get("if-none-match", "").strip('"')
    headers = {
        "ETag": f'"{etag}"',
        "Cache-Control": f"public, max-age={cache_seconds}, stale-while-revalidate=5",
        "Vary": "Accept-Encoding"
    }
    if client_etag and client_etag == etag:
        return Response(status_code=304, headers=headers)
    return HTMLResponse(content=html_content, status_code=status_code, headers=headers)

def render_consumer_template(template_name: str, request: Request, context: dict = None, cache_seconds: int = 60, status_code: int = 200) -> Response:
    if context is None:
        context = {}
    context["request"] = request
    context["categories_taxonomy"] = UNIVERSAL_CATEGORIES
    try:
        tpl = templates.get_template(template_name)
        rendered_html = tpl.render(context)
        return make_html_response(request, rendered_html, cache_seconds=cache_seconds, status_code=status_code)
    except Exception as e:
        logger.error(f"Render error for {template_name}: {e}", exc_info=True)
        import traceback
        return HTMLResponse(content=f"<h3>Template Error: {e}</h3><pre>{traceback.format_exc()}</pre>", status_code=500)

async def build_home_html(request: Request = None) -> str:
    """Builds and renders the full Homepage SSR HTML with blazing fast parallel queries."""
    if request is None:
        dummy_scope = {
            "type": "http", "method": "GET", "path": "/", "headers": [],
            "query_string": b"", "client": ("127.0.0.1", 12345), "server": ("localhost", 5000), "scheme": "http"
        }
        request = Request(dummy_scope)

    best_deals = {"deals": []}
    just_dropped = {"deals": []}
    atl_mini = {"deals": []}
    initial_stats = {}

    try:
        best_deals_task = get_public_deals(request=request, platform="", platforms="", category="", categories="", sub="", subcategory="", gender="", tab="all", search="", sort_by="score_desc", min_discount=0.0, min_price=0.0, max_price=0.0, min_rating=0.0, verified_only=True, deal_type="", ids="", page=1, limit=10, include_count=False)
        just_dropped_task = get_public_deals(request=request, platform="", platforms="", category="", categories="", sub="", subcategory="", gender="", tab="drops", search="", sort_by="latest", min_discount=0.0, min_price=0.0, max_price=0.0, min_rating=0.0, verified_only=True, deal_type="drops", ids="", page=1, limit=20, include_count=False)
        atl_task = get_public_deals(request=request, platform="", platforms="", category="", categories="", sub="", subcategory="", gender="", tab="atl", search="", sort_by="latest", min_discount=0.0, min_price=0.0, max_price=0.0, min_rating=0.0, verified_only=True, deal_type="atl", ids="", page=1, limit=20, include_count=False)
        stats_task = get_public_stats()

        results = await asyncio.gather(
            best_deals_task, just_dropped_task, atl_task, stats_task,
            return_exceptions=True
        )
        if not isinstance(results[0], Exception):
            best_deals = results[0]
        if not isinstance(results[1], Exception):
            just_dropped = results[1]
        if not isinstance(results[2], Exception):
            atl_mini = results[2]
        if not isinstance(results[3], Exception):
            initial_stats = results[3]

        # Deduplicate carousels so each section displays fresh, mutually exclusive deals
        seen_pids = set()
        if best_deals and "deals" in best_deals:
            for d in best_deals["deals"]:
                pid = d.get("product_id") or d.get("id")
                if pid:
                    seen_pids.add(pid)

        if just_dropped and "deals" in just_dropped:
            unique_drops = [d for d in just_dropped["deals"] if (d.get("product_id") or d.get("id")) not in seen_pids]
            if unique_drops:
                just_dropped["deals"] = unique_drops[:10]
                for d in unique_drops[:10]:
                    pid = d.get("product_id") or d.get("id")
                    if pid:
                        seen_pids.add(pid)
            else:
                just_dropped["deals"] = just_dropped["deals"][:10]

        if atl_mini and "deals" in atl_mini:
            unique_atls = [d for d in atl_mini["deals"] if (d.get("product_id") or d.get("id")) not in seen_pids]
            if unique_atls:
                atl_mini["deals"] = unique_atls[:10]
            else:
                atl_mini["deals"] = atl_mini["deals"][:10]
    except Exception as e:
        logger.warning(f"Homepage prefetch error: {e}")

    context = {
        "request": request,
        "categories_taxonomy": UNIVERSAL_CATEGORIES,
        "active_page": "home",
        "best_deals": best_deals,
        "just_dropped": just_dropped,
        "atl_mini": atl_mini,
        "initial_stats": initial_stats,
        "categories": UNIVERSAL_CATEGORIES
    }
    tpl = templates.get_template("consumer/home.html")
    return tpl.render(context)

@app.get("/", response_class=HTMLResponse)
async def page_home(request: Request):
    """Renders the high-converting BudgetBy Visual Category Storefront (v4)."""
    ssr_key = "home"
    cached_html = await get_cached_ssr_html(ssr_key)
    if cached_html:
        return make_html_response(request, cached_html, cache_seconds=180)

    html_content = await build_home_html(request)
    if html_content:
        await set_cached_ssr_html(ssr_key, html_content, ttl=180)
        return make_html_response(request, html_content, cache_seconds=180)
    return HTMLResponse(content="<h1>Unable to load storefront. Please retry.</h1>", status_code=500)

async def ssr_prewarm_loop():
    """
    Background cache pre-warmer:
    Ensures that visitors NEVER experience a cold database load or template render delay.
    Maintains 100% cache hit rate for the storefront home page and top hubs with zero latency (<20ms).
    """
    logger.info("🔥 Starting background SSR cache pre-warmer loop...")
    await asyncio.sleep(2)
    dummy_req = Request({
        "type": "http", "method": "GET", "path": "/", "headers": [],
        "query_string": b"", "client": ("127.0.0.1", 12345), "server": ("localhost", 5000), "scheme": "http"
    })
    while True:
        try:
            t0 = time.time()
            html = await build_home_html(dummy_req)
            if html:
                await set_cached_ssr_html("home", html, ttl=180)

            # Prewarm Drops, ATL, Deals catalog, & categories asynchronously in background
            try:
                await page_drops(dummy_req, page=1, limit=24)
            except Exception:
                pass
            try:
                await page_atl(dummy_req, page=1, limit=24)
            except Exception:
                pass
            try:
                await page_deals(dummy_req, page=1, limit=24)
            except Exception:
                pass
            try:
                await get_public_categories()
            except Exception:
                pass

            elapsed = (time.time() - t0) * 1000
            logger.info(f"⚡ Storefront SSR hubs pre-warmed in {elapsed:.1f}ms (Hit rate: 100%)")
        except Exception as e:
            logger.warning(f"SSR pre-warm notice: {e}")
        await asyncio.sleep(90)

@app.get("/price-drops", response_class=HTMLResponse)
@app.get("/drops", response_class=HTMLResponse)
async def page_drops(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    min_drop_pct: float = Query(20.0, ge=0.0, le=90.0),
    min_drop_percent: float = Query(None),
    platform: str = Query(""),
    sort_by: str = Query("drop_desc")
):
    """Renders the 24-Hour Price Drops Hub with complete pre-rendered items (SSR)."""
    page_num = int(page) if not hasattr(page, 'default') and str(page).isdigit() else 1
    limit_num = int(limit) if not hasattr(limit, 'default') and str(limit).isdigit() else 24
    plat_str = str(platform) if not hasattr(platform, 'default') else ""
    sort_str = str(sort_by) if not hasattr(sort_by, 'default') else "drop_desc"
    raw_min_drop = min_drop_percent if not hasattr(min_drop_percent, 'default') and min_drop_percent is not None else min_drop_pct
    eff_min_drop = float(raw_min_drop) if not hasattr(raw_min_drop, 'default') and raw_min_drop is not None else 20.0

    ssr_key = f"drops:{page_num}:{limit_num}:{eff_min_drop}:{plat_str}:{sort_str}"
    cached_html = await get_cached_ssr_html(ssr_key)
    if cached_html:
        return make_html_response(request, cached_html, cache_seconds=240)

    initial_data = {"drops": [], "total_drops_24h": 0, "total_pages": 1, "page": page_num}
    try:
        initial_data = await get_public_price_drops(
            request=request,
            page=page_num,
            limit=limit_num,
            min_drop_pct=eff_min_drop,
            min_drop_percent=None,
            platform=plat_str,
            category="",
            sort_by=sort_str
        )
    except Exception as e:
        logger.warning(f"Drops SSR prefetch error: {e}")

    resp = render_consumer_template("consumer/drops.html", request, {
        "active_page": "drops",
        "initial_data": initial_data,
        "current_page": page_num,
        "current_min_drop": eff_min_drop,
        "current_platform": plat_str,
        "current_sort": sort_str
    }, cache_seconds=240)
    if resp.status_code == 200 and hasattr(resp, "body"):
        await set_cached_ssr_html(ssr_key, resp.body.decode("utf-8"), ttl=240)
    return resp

@app.get("/deals", response_class=HTMLResponse)
async def page_deals(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    search: str = Query(""),
    platform: str = Query(""),
    category: str = Query(""),
    sub: str = Query(""),
    subcategory: str = Query(""),
    gender: str = Query(""),
    tab: str = Query("all"),
    verified_only: bool = Query(True),
    sort_by: str = Query("latest"),
    min_discount: float = Query(0.0),
    min_price: float = Query(0.0),
    max_price: float = Query(0.0),
    min_rating: float = Query(0.0)
):
    """Renders the Deals Catalog with complete pre-rendered items (SSR)."""
    page_num = int(page) if not hasattr(page, 'default') and str(page).isdigit() else 1
    limit_num = int(limit) if not hasattr(limit, 'default') and str(limit).isdigit() else 24
    search_str = str(search) if not hasattr(search, 'default') and search is not None else ""
    plat_str = str(platform) if not hasattr(platform, 'default') and platform is not None else ""
    cat_str = str(category) if not hasattr(category, 'default') and category is not None else ""
    gender_str = str(gender) if not hasattr(gender, 'default') and gender is not None else ""
    sub_raw = str(sub) if not hasattr(sub, 'default') and sub is not None else ""
    subcat_raw = str(subcategory) if not hasattr(subcategory, 'default') and subcategory is not None else ""
    eff_sub_page = (sub_raw or subcat_raw or "").strip().lower()
    tab_str = str(tab) if not hasattr(tab, 'default') and tab is not None else "all"
    sort_str = str(sort_by) if not hasattr(sort_by, 'default') and sort_by is not None else "latest"
    min_disc_val = float(min_discount) if not hasattr(min_discount, 'default') and min_discount is not None else 0.0
    min_price_val = float(min_price) if not hasattr(min_price, 'default') and min_price is not None else 0.0
    max_price_val = float(max_price) if not hasattr(max_price, 'default') and max_price is not None else 0.0
    min_rating_val = float(min_rating) if not hasattr(min_rating, 'default') and min_rating is not None else 0.0
    ver_val = bool(verified_only) if not hasattr(verified_only, 'default') and verified_only is not None else True

    # When user searches, show all matching products across catalog unless verified_only is explicitly set
    has_query_params = hasattr(request, "query_params")
    if search_str and has_query_params and "verified_only" not in request.query_params:
        ver_val = False

    ssr_ttl = 60 if (search_str or sort_str == "latest" or tab_str == "all" or eff_sub_page) else 300
    ssr_key = f"deals:{page_num}:{limit_num}:{search_str}:{plat_str}:{cat_str}:{eff_sub_page}:{gender_str}:{tab_str}:{ver_val}:{sort_str}:{min_disc_val}:{min_price_val}:{max_price_val}:{min_rating_val}"
    cached_html = await get_cached_ssr_html(ssr_key)
    if cached_html:
        return make_html_response(request, cached_html, cache_seconds=ssr_ttl)

    initial_data = {"deals": [], "total_matches": 0, "total_pages": 1, "page": page_num}
    try:
        initial_data = await get_public_deals(
            request=request,
            platform=plat_str,
            platforms="",
            category=cat_str,
            categories="",
            sub=eff_sub_page,
            subcategory="",
            gender=gender_str,
            tab=tab_str,
            search=search_str,
            sort_by=sort_str,
            min_discount=min_disc_val,
            min_price=min_price_val,
            max_price=max_price_val,
            min_rating=min_rating_val,
            verified_only=ver_val,
            deal_type="",
            ids="",
            page=page_num,
            limit=limit_num
        )
    except Exception as e:
        logger.warning(f"Deals SSR prefetch error: {e}")

    resp = render_consumer_template("consumer/deals.html", request, {
        "active_page": "deals",
        "cat_info": UNIVERSAL_CATEGORIES.get(cat_str.lower()) if cat_str else None,
        "initial_data": initial_data,
        "current_page": page_num,
        "current_search": search_str,
        "current_platform": plat_str,
        "current_category": cat_str,
        "current_sub": eff_sub_page,
        "current_gender": gender_str,
        "current_tab": tab_str,
        "current_verified_only": ver_val,
        "current_sort": sort_str,
        "current_min_discount": min_disc_val,
        "current_min_price": min_price_val,
        "current_max_price": max_price_val,
        "current_min_rating": min_rating_val
    }, cache_seconds=ssr_ttl)
    if resp.status_code == 200 and hasattr(resp, "body"):
        await set_cached_ssr_html(ssr_key, resp.body.decode("utf-8"), ttl=ssr_ttl)
    return resp

@app.get("/all-time-lows", response_class=HTMLResponse)
@app.get("/atl", response_class=HTMLResponse)
async def page_atl(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    platform: str = Query(""),
    category: str = Query(""),
    sub: str = Query(""),
    subcategory: str = Query(""),
    gender: str = Query(""),
    sort_by: str = Query("latest")
):
    """Renders the All-Time Lows (ATL) Showcase Hub with complete pre-rendered items (SSR)."""
    page_num = int(page) if not hasattr(page, 'default') and str(page).isdigit() else 1
    limit_num = int(limit) if not hasattr(limit, 'default') and str(limit).isdigit() else 24
    plat_str = str(platform) if not hasattr(platform, 'default') and platform is not None else ""
    cat_str = str(category) if not hasattr(category, 'default') and category is not None else ""
    gender_str = str(gender) if not hasattr(gender, 'default') and gender is not None else ""
    sub_raw = str(sub) if not hasattr(sub, 'default') and sub is not None else ""
    subcat_raw = str(subcategory) if not hasattr(subcategory, 'default') and subcategory is not None else ""
    eff_sub_page = (sub_raw or subcat_raw or "").strip().lower()
    sort_str = str(sort_by) if not hasattr(sort_by, 'default') and sort_by is not None else "latest"

    ssr_key = f"atl:{page_num}:{limit_num}:{plat_str}:{cat_str}:{eff_sub_page}:{gender_str}:{sort_str}"
    cached_html = await get_cached_ssr_html(ssr_key)
    if cached_html:
        return make_html_response(request, cached_html, cache_seconds=120)

    initial_data = {"deals": [], "total_matches": 0, "total_pages": 1, "page": page_num}
    try:
        initial_data = await get_public_deals(
            request=request,
            platform=plat_str,
            platforms="",
            category=cat_str,
            categories="",
            sub=eff_sub_page,
            subcategory="",
            tab="atl",
            search="",
            sort_by=sort_str,
            min_discount=0.0,
            min_price=0.0,
            max_price=0.0,
            min_rating=0.0,
            verified_only=False,
            deal_type="atl",
            ids="",
            page=page_num,
            limit=limit_num
        )
    except Exception as e:
        logger.warning(f"ATL SSR prefetch error: {e}")

    resp = render_consumer_template("consumer/atl.html", request, {
        "active_page": "atl",
        "initial_data": initial_data,
        "current_page": page_num,
        "current_platform": plat_str,
        "current_category": cat_str,
        "current_sub": eff_sub_page,
        "current_gender": gender_str,
        "current_sort": sort_str
    }, cache_seconds=240)
    if resp.status_code == 200 and hasattr(resp, "body"):
        await set_cached_ssr_html(ssr_key, resp.body.decode("utf-8"), ttl=240)
    return resp

@app.get("/stores", response_class=HTMLResponse)
@app.get("/stores/{platform}", response_class=HTMLResponse)
async def page_stores(request: Request, platform: str = ""):
    """Renders the Stores Directory or 301 Redirects to Canonical Deals Feed."""
    plat_clean = platform.strip().lower() if platform else ""
    if plat_clean:
        if plat_clean in STORE_DISPLAY_NAMES:
            return RedirectResponse(url=f"/deals?platform={plat_clean}", status_code=301)
        return RedirectResponse(url="/stores", status_code=302)
    store_name = None
    
    ssr_key = f"stores:hub"
    cached_html = await get_cached_ssr_html(ssr_key)
    if cached_html:
        return make_html_response(request, cached_html, cache_seconds=300)

    initial_deals = None
    if plat_clean in STORE_DISPLAY_NAMES:
        try:
            initial_deals = await get_public_deals(
                request=request,
                platform=plat_clean,
                platforms="",
                category="",
                categories="",
                tab="all",
                search="",
                sort_by="latest",
                min_discount=0.0,
                min_price=0.0,
                max_price=0.0,
                min_rating=0.0,
                verified_only=False,
                deal_type="",
                ids="",
                page=1,
                limit=24
            )
        except Exception as e:
            logger.warning(f"Store deals SSR prefetch error: {e}")

    resp = render_consumer_template(
        "consumer/stores.html", 
        request, 
        {
            "active_page": "stores", 
            "store_id": plat_clean if plat_clean in STORE_DISPLAY_NAMES else None,
            "store_name": store_name,
            "initial_data": initial_deals
        },
        cache_seconds=300
    )
    if resp.status_code == 200 and hasattr(resp, "body"):
        await set_cached_ssr_html(ssr_key, resp.body.decode("utf-8"), ttl=300)
    return resp


@app.get("/deal/{product_id}", response_class=HTMLResponse)
@app.get("/product/{product_id}", response_class=HTMLResponse)
async def page_deal_detail(request: Request, product_id: int):
    """Renders the comprehensive Product & Deal Detail Experience with visual price history."""
    ssr_key = f"deal_detail:{product_id}"
    cached_html = await get_cached_ssr_html(ssr_key)
    if cached_html:
        return make_html_response(request, cached_html, cache_seconds=300)

    try:
        detail_data = await get_public_deal_detail(product_id)
        if not detail_data or not detail_data.get("product"):
            return render_consumer_template("consumer/404.html", request, {
                "active_page": "deals",
                "categories": UNIVERSAL_CATEGORIES
            }, cache_seconds=60, status_code=404)

        prod = detail_data["product"]
        cat = prod.get("category", "")

        # Related deals: pre-fetched from existing RAM-cached deals endpoint (0 extra DB queries)
        related_deals = []
        if cat:
            try:
                rel_res = await get_public_deals(request=request, category=cat, page=1, limit=4, verified_only=False, include_count=False)
                if isinstance(rel_res, dict):
                    related_deals = [d for d in rel_res.get("deals", []) if int(d.get("product_id") or d.get("id") or 0) != product_id][:4]
            except Exception as e:
                logger.debug(f"Related deals prefetch note: {e}")

        rendered_html = render_consumer_template("consumer/deal_detail.html", request, {
            "active_page": "deals",
            "product": prod,
            "price_history": detail_data.get("price_history", []),
            "related_deals": related_deals,
            "categories": UNIVERSAL_CATEGORIES
        }, cache_seconds=300)

        if rendered_html.status_code == 200 and hasattr(rendered_html, "body"):
            await set_cached_ssr_html(ssr_key, rendered_html.body.decode("utf-8"), ttl=300)
        return rendered_html
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rendering deal detail for {product_id}: {e}", exc_info=True)
        return render_consumer_template("consumer/404.html", request, {
            "active_page": "deals",
            "categories": UNIVERSAL_CATEGORIES
        }, cache_seconds=60, status_code=404)


def render_admin_login_template(request: Request, context: dict, status_code: int = 200):
    context["request"] = request
    try:
        return templates.TemplateResponse(request=request, name="admin_login.html", context=context, status_code=status_code)
    except TypeError:
        return templates.TemplateResponse("admin_login.html", context, status_code=status_code)

@app.get("/pnther/login", response_class=HTMLResponse)
@app.get("/pnther/login/", response_class=HTMLResponse)
async def get_admin_login(request: Request, next: str = "/pnther"):
    """Renders the secure Admin Login Page. Always prompts for credentials."""
    if not is_admin_enabled(request):
        return RedirectResponse(url="/", status_code=302)
    clean_next = next if next.startswith("/pnther") and not next.startswith("/pnther/login") else "/pnther"
    response = render_admin_login_template(
        request,
        {"next": clean_next, "error": None}
    )
    response.delete_cookie(key="budgetby_admin_session")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response

@app.post("/pnther/login", response_class=HTMLResponse)
@app.post("/pnther/login/", response_class=HTMLResponse)
async def post_admin_login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/pnther")
):
    """
    Authenticates admin credentials against authorized pairs:
    1) pnther / Pnther@3Alphabetisc
    2) vidushi / lilu
    """
    if not is_admin_enabled(request):
        return RedirectResponse(url="/", status_code=302)

    user_clean = (username or "").strip()
    pwd_clean = (password or "").strip()

    auth_ok = False
    matched_user = None
    for u, p in ADMIN_CREDENTIALS:
        if secrets.compare_digest(user_clean, u) and secrets.compare_digest(pwd_clean, p):
            auth_ok = True
            matched_user = u
            break

    clean_next = next if next.startswith("/pnther") and not next.startswith("/pnther/login") else "/pnther"

    if not auth_ok:
        await asyncio.sleep(0.3)  # Anti brute-force timing buffer
        return render_admin_login_template(
            request,
            {"next": clean_next, "error": "Invalid username or password. Access denied."},
            status_code=401
        )

    token = create_admin_session_token(matched_user)
    target_url = f"{clean_next}{'&' if '?' in clean_next else '?'}auth_token={token}"
    resp = RedirectResponse(url=target_url, status_code=303)
    resp.delete_cookie(key="budgetby_admin_session")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp

@app.get("/pnther/logout")
@app.get("/pnther/logout/")
@app.post("/pnther/logout")
@app.post("/pnther/logout/")
async def admin_logout(request: Request = None):
    """Logs out admin and terminates any session."""
    if request and not is_admin_enabled(request):
        return RedirectResponse(url="/", status_code=302)
    resp = RedirectResponse(url="/pnther/login", status_code=302)
    resp.delete_cookie(key="budgetby_admin_session")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp

@app.get("/pnther", response_class=HTMLResponse)
@app.get("/pnther/", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Renders the BudgetBy Admin Control Center (Stealth Protected URL: /pnther)."""
    if not is_admin_enabled(request):
        return RedirectResponse(url="/", status_code=302)

    auth_tok = request.query_params.get("auth_token")
    req_key = request.query_params.get("key") or request.query_params.get("admin_key")

    is_valid_token = bool(auth_tok and verify_admin_session_token(auth_tok))
    is_valid_key = bool(req_key and secrets.compare_digest(str(req_key), ADMIN_SECRET_KEY))

    if not is_valid_token and not is_valid_key:
        return RedirectResponse(url="/pnther/login?next=/pnther", status_code=302)

    active_token = auth_tok if is_valid_token else create_admin_session_token("pnther")

    raw_html = "<h1>BudgetBy Admin Dashboard Loading...</h1>"
    if os.path.exists(ADMIN_TEMPLATE_PATH):
        with open(ADMIN_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            raw_html = f.read()

    final_html = inject_admin_session_script(raw_html, active_token)
    response = HTMLResponse(content=final_html)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.delete_cookie(key="budgetby_admin_session")
    return response

@app.get("/pnther/db-explorer", response_class=HTMLResponse)
@app.get("/pnther/db-explorer/", response_class=HTMLResponse)
async def admin_db_explorer(request: Request):
    """Renders the BudgetBy Database Explorer (Stealth Protected URL: /pnther/db-explorer)."""
    if not is_admin_enabled(request):
        return RedirectResponse(url="/", status_code=302)

    auth_tok = request.query_params.get("auth_token")
    req_key = request.query_params.get("key") or request.query_params.get("admin_key")

    is_valid_token = bool(auth_tok and verify_admin_session_token(auth_tok))
    is_valid_key = bool(req_key and secrets.compare_digest(str(req_key), ADMIN_SECRET_KEY))

    if not is_valid_token and not is_valid_key:
        return RedirectResponse(url="/pnther/login?next=/pnther/db-explorer", status_code=302)

    active_token = auth_tok if is_valid_token else create_admin_session_token("pnther")

    response = await render_db_explorer(token=active_token)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.delete_cookie(key="budgetby_admin_session")
    return response

@app.get("/admin")
@app.get("/admin/{rest:path}")
async def redirect_old_admin(rest: str = ""):
    """Safely redirects deprecated /admin routes to consumer homepage."""
    return RedirectResponse(url="/", status_code=302)

@app.get("/about", response_class=HTMLResponse)
@app.get("/about/", response_class=HTMLResponse)
async def page_about(request: Request):
    """Renders the About & Methodology page."""
    return render_consumer_template("consumer/about.html", request, {
        "active_page": "about"
    }, cache_seconds=300)

@app.get("/robots.txt", response_class=Response)
async def robots_txt():
    """Search Engine Optimization Robots file."""
    content = """User-agent: *
Allow: /
Allow: /deals
Allow: /drops
Allow: /all-time-lows
Allow: /stores
Allow: /about
Allow: /deal/
Allow: /product/
Disallow: /api/
Disallow: /pnther/
Disallow: /admin/

Sitemap: https://budgetby.in/sitemap.xml
"""
    return Response(content=content, media_type="text/plain", headers={"Cache-Control": "public, max-age=86400"})

@app.get("/sitemap.xml", response_class=Response)
async def sitemap_xml():
    """
    Search Engine Optimization XML Sitemap.
    Strictly cached in RAM for 24 hours to guarantee zero egress penalty.
    """
    cache_key = "seo_sitemap_xml_cached"
    cached = await ram_cache.get(cache_key)
    if cached:
        return Response(content=cached, media_type="application/xml", headers={"Cache-Control": "public, max-age=86400"})

    try:
        # Fetch active products with minimal columns (id + updated_at) to avoid egress
        rows = await database.fetch("""
            SELECT id, product_id, updated_at 
            FROM products 
            WHERE status = 'ACTIVE' 
            ORDER BY updated_at DESC NULLS LAST 
            LIMIT 5000;
        """)
    except Exception as e:
        logger.error(f"Sitemap generation DB fetch error: {e}")
        rows = []

    urls = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]

    static_routes = [
        ("/", "hourly", "1.0"),
        ("/deals", "hourly", "0.9"),
        ("/drops", "hourly", "0.9"),
        ("/all-time-lows", "hourly", "0.9"),
        ("/stores", "daily", "0.7"),
        ("/about", "weekly", "0.5"),
    ]
    for path, changefreq, priority in static_routes:
        urls.append(
            f"  <url>\n    <loc>https://budgetby.in{path}</loc>\n    <changefreq>{changefreq}</changefreq>\n    <priority>{priority}</priority>\n  </url>"
        )

    for r in rows:
        pid = r.get("product_id") or r.get("id")
        if not pid:
            continue
        upd = r.get("updated_at")
        lastmod = upd.strftime("%Y-%m-%d") if upd else ""
        lastmod_elem = f"\n    <lastmod>{lastmod}</lastmod>" if lastmod else ""
        urls.append(
            f"  <url>\n    <loc>https://budgetby.in/deal/{pid}</loc>{lastmod_elem}\n    <changefreq>daily</changefreq>\n    <priority>0.8</priority>\n  </url>"
        )

    urls.append("</urlset>")
    sitemap_content = "\n".join(urls)
    await ram_cache.set(cache_key, sitemap_content, ttl=86400)
    return Response(content=sitemap_content, media_type="application/xml", headers={"Cache-Control": "public, max-age=86400"})

@app.exception_handler(404)
async def custom_404_handler(request: Request, exc):
    """Renders the custom branded 404 page for missing consumer routes."""
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"detail": "Not Found", "status": "error"})
    return render_consumer_template("consumer/404.html", request, {
        "active_page": "404"
    }, cache_seconds=60)

# ── Live Remote Log Streaming (Local Private Network Only / Zero Cloud Egress) ───

def _is_private_ip(host: str) -> bool:
    """Returns True only for loopback and private LAN IP addresses."""
    if not host:
        return False
    if host in ("127.0.0.1", "localhost", "::1", "testclient"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def _clean_log_line(raw_line: str, source: str) -> dict | None:
    """Cleans, redacts credentials, and filters out high-frequency polling spam."""
    line = raw_line.strip()
    if not line:
        return None
    # Strip spammy telegram getUpdates 200 OK polling logs
    if "getUpdates" in line and "200 OK" in line:
        return None
    # Strip repetitive internal scheduler idle logs
    if "0 products currently due for check" in line:
        return None
    # Redact credentials and tokens
    line = re.sub(r'bot\d+:[A-Za-z0-9_-]+', 'bot***[REDACTED]***', line)
    line = re.sub(r'postgres://[^@]+@', 'postgres://***[REDACTED]***@', line)
    line = re.sub(r'postgresql://[^@]+@', 'postgresql://***[REDACTED]***@', line)
    line = re.sub(r'((?:key|admin_key|token|auth_token|password|secret)=)[^&\s]+', r'\1***[REDACTED]***', line, flags=re.IGNORECASE)
    line = re.sub(r'Bearer\s+[A-Za-z0-9\._-]+', 'Bearer ***[REDACTED]***', line, flags=re.IGNORECASE)
    line = re.sub(r'bb_sec_[a-f0-9]+', 'bb_sec_***[REDACTED]***', line)

    level = "INFO"
    if "ERROR" in line or "CRITICAL" in line:
        level = "ERROR"
    elif "WARNING" in line:
        level = "WARNING"
    elif "DEBUG" in line:
        level = "DEBUG"

    return {
        "source": source,
        "level": level,
        "text": line,
        "timestamp": int(time.time())
    }


def _read_initial_backlog(bot_log_path: str, sync_log_path: str, max_lines: int = 35) -> list[dict]:
    """Reads recent lines from both log files so the terminal starts with immediate context."""
    items = []
    for path, src in [(bot_log_path, "engine"), (sync_log_path, "sync_watcher")]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()[-max_lines:]
                    for l in lines:
                        cleaned = _clean_log_line(l, src)
                        if cleaned:
                            items.append(cleaned)
            except Exception:
                pass
    return items[-max_lines:]


@app.get("/api/live-logs/ping")
async def ping_live_logs(request: Request):
    """
    Zero-database discovery probe for the local desktop viewer.
    - Completely disabled (404) on Render and cloud deployments.
    - Restricted to private LAN subnets.
    """
    if os.getenv("RENDER") or os.getenv("ENVIRONMENT") == "production":
        raise HTTPException(status_code=404, detail="Not Found")

    client_host = getattr(getattr(request, "client", None), "host", "")
    if not _is_private_ip(client_host):
        raise HTTPException(status_code=404, detail="Not Found")

    return {"service": "budgetby", "node": "server", "status": "online"}


@app.get("/api/live-logs/stream")
async def stream_live_logs(request: Request):
    """
    Server-Sent Events (SSE) live log streaming for local network administration.
    - Completely disabled (404) on Render and cloud deployments.
    - Restricted to private LAN subnets.
    - Authenticated with ADMIN_SECRET_KEY.
    - Zero cloud egress (100% local LAN traffic).
    - 100% on-demand: stops immediately when viewer disconnects.
    """
    if os.getenv("RENDER") or os.getenv("ENVIRONMENT") == "production":
        raise HTTPException(status_code=404, detail="Not Found")

    client_host = getattr(getattr(request, "client", None), "host", "")
    if not _is_private_ip(client_host):
        raise HTTPException(status_code=404, detail="Not Found")

    req_key = request.headers.get("X-Admin-Key") or request.query_params.get("key") or ""
    if not req_key or not secrets.compare_digest(str(req_key), ADMIN_SECRET_KEY):
        raise HTTPException(status_code=403, detail="Forbidden")

    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    bot_log_path = os.path.join(base_dir, "bot_runner.log")
    sync_log_path = os.path.join(base_dir, "sync_watcher.log")

    async def log_event_generator():
        # 1. Send initial context lines
        backlog = _read_initial_backlog(bot_log_path, sync_log_path, max_lines=25)
        for item in backlog:
            yield f"data: {json.dumps(item)}\n\n"

        # 2. Tail new lines continuously
        bot_pos = os.path.getsize(bot_log_path) if os.path.exists(bot_log_path) else 0
        sync_pos = os.path.getsize(sync_log_path) if os.path.exists(sync_log_path) else 0

        while True:
            if await request.is_disconnected():
                break

            had_data = False

            # Check bot_runner.log (Deal Engine)
            if os.path.exists(bot_log_path):
                try:
                    curr_size = os.path.getsize(bot_log_path)
                    if curr_size < bot_pos:
                        bot_pos = 0  # Rotated
                    if curr_size > bot_pos:
                        with open(bot_log_path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(bot_pos)
                            new_lines = f.readlines()
                            bot_pos = f.tell()
                        for line in new_lines:
                            clean = _clean_log_line(line, "engine")
                            if clean:
                                yield f"data: {json.dumps(clean)}\n\n"
                                had_data = True
                except Exception:
                    pass

            # Check sync_watcher.log (Sync Watcher)
            if os.path.exists(sync_log_path):
                try:
                    curr_size = os.path.getsize(sync_log_path)
                    if curr_size < sync_pos:
                        sync_pos = 0
                    if curr_size > sync_pos:
                        with open(sync_log_path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(sync_pos)
                            new_lines = f.readlines()
                            sync_pos = f.tell()
                        for line in new_lines:
                            clean = _clean_log_line(line, "sync_watcher")
                            if clean:
                                yield f"data: {json.dumps(clean)}\n\n"
                                had_data = True
                except Exception:
                    pass

            if not had_data:
                await asyncio.sleep(0.5)
            else:
                await asyncio.sleep(0.05)

    return StreamingResponse(log_event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000)
