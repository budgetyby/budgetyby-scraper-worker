"""
BudgetBy — Unified Multi-Store Real-Time Price History Puller
Covers: Amazon, Flipkart, Myntra, Ajio, Nykaa
Reliable, 100% Free, Zero Fake Data, Automated Seeding & Brand-New Product Detection.
"""

import asyncio
import datetime
import logging
import re
import urllib.parse
from typing import Dict, Any, List, Optional
from curl_cffi.requests import AsyncSession

logger = logging.getLogger("budgetby.engine.price_history_puller")

# Regex patterns for extracting canonical product identifiers
RE_AMAZON_ASIN = re.compile(r'/(?:dp|gp/product)/([A-Z0-9]{10})', re.IGNORECASE)
RE_FLIPKART_PID = re.compile(r'pid=([a-zA-Z0-9]+)', re.IGNORECASE)
RE_FLIPKART_FSN = re.compile(r'/p/([^/?]+)', re.IGNORECASE)
RE_MYNTRA_ID = re.compile(r'/(\d{5,12})(?:/buy|\?|$)', re.IGNORECASE)
RE_AJIO_CODE = re.compile(r'/p/(\d{10,15})', re.IGNORECASE)


def canonicalize_product_url(url: str, platform: str) -> str:
    """Extract clean canonical URL for product trackers."""
    if not url:
        return ""
    p = (platform or "").lower()
    
    if "amazon" in p or "amazon.in" in url:
        m = RE_AMAZON_ASIN.search(url)
        if m:
            return f"https://www.amazon.in/dp/{m.group(1)}"
    elif "flipkart" in p or "flipkart.com" in url:
        m_pid = RE_FLIPKART_PID.search(url)
        if m_pid:
            return f"https://www.flipkart.com/product/p/itm?pid={m_pid.group(1)}"
        m_fsn = RE_FLIPKART_FSN.search(url)
        if m_fsn:
            return f"https://www.flipkart.com/p/{m_fsn.group(1)}"
    elif "myntra" in p or "myntra.com" in url:
        m = RE_MYNTRA_ID.search(url)
        if m:
            return f"https://www.myntra.com/{m.group(1)}"
    elif "ajio" in p or "ajio.com" in url:
        m = RE_AJIO_CODE.search(url)
        if m:
            return f"https://www.ajio.com/p/{m.group(1)}"
            
    return url.split("?")[0] if "?" in url else url


async def fetch_from_pricediff(session: AsyncSession, url: str) -> Optional[Dict[str, Any]]:
    """
    Fetch price history and metadata from pricediff.in (Free multi-store tracker).
    Supports: Amazon, Flipkart, Myntra, Ajio.
    """
    try:
        api_url = f"https://pricediff.in/api/products/lookup/?url={urllib.parse.quote(url)}"
        headers = {
            "Referer": "https://pricediff.in/",
            "Origin": "https://pricediff.in",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = await session.get(api_url, headers=headers, timeout=2.5)
        if r.status_code == 200:
            data = r.json()
            prod = data.get("product") or {}
            history_raw = data.get("price_history") or []
            
            clean_history = []
            for item in history_raw:
                try:
                    price_val = float(item.get("price", 0))
                    checked_at = item.get("checked_at", "")
                    if price_val > 0 and checked_at:
                        date_str = str(checked_at).split("T")[0].split()[0]
                        clean_history.append({"date": date_str, "price": price_val})
                except Exception:
                    continue
            
            # Deduplicate by date (keep minimum price for each day)
            day_map = {}
            for pt in clean_history:
                d = pt["date"]
                if d not in day_map or pt["price"] < day_map[d]["price"]:
                    day_map[d] = pt
            sorted_history = sorted(day_map.values(), key=lambda x: x["date"])
            
            lowest = float(prod.get("lowest_price")) if prod.get("lowest_price") else None
            mrp = float(prod.get("mrp")) if prod.get("mrp") else None
            
            return {
                "source": "pricediff",
                "points": sorted_history,
                "lowest_price": lowest,
                "mrp": mrp,
                "is_brand_new": len(sorted_history) <= 1
            }
    except Exception as e:
        logger.debug(f"pricediff lookup failed for {url}: {e}")
    return None


async def fetch_from_pricehistory_app(session: AsyncSession, url: str) -> Optional[Dict[str, Any]]:
    """
    Fetch deep multi-year price history from pricehistory.app.
    Supports: Amazon India and Flipkart.
    """
    try:
        r1 = await session.post("https://pricehistory.app/api/search", json={"url": url}, timeout=2.2)
        if r1.status_code == 200:
            d1 = r1.json()
            # Explicit signal for brand new / unindexed product
            if d1.get("status") is False and "Product Added to Track" in d1.get("message", ""):
                return {
                    "source": "pricehistory_app",
                    "points": [],
                    "is_brand_new": True,
                    "message": d1.get("message")
                }
            
            if d1.get("status") and d1.get("code"):
                code = d1["code"]
                p_url = f"https://pricehistory.app/p/{code}"
                pr = await session.get(p_url, timeout=2.2)
                
                m_token = re.search(r'FetchHeaders\.append\(["\']token["\'],\s*["\']([^"\']+)["\']\)', pr.text)
                m_page = re.search(r'FetchHeaders\.append\(["\']page["\'],\s*["\']([^"\']+)["\']\)', pr.text)
                
                if m_token and m_page:
                    page_id = m_page.group(1)
                    token = m_token.group(1)
                    
                    r3 = await session.post(
                        f"https://pricehistory.app/api/price/{page_id}",
                        headers={
                            "Content-Type": "application/json",
                            "page": page_id,
                            "token": token,
                            "Referer": p_url,
                            "Origin": "https://pricehistory.app"
                        },
                        timeout=2.2
                    )
                    if r3.status_code == 200:
                        data = r3.json()
                        raw_pts = data.get("History", {}).get("Price", [])
                        
                        clean_history = []
                        for pt in raw_pts:
                            try:
                                p_val = float(pt.get("y", 0))
                                x_dt = pt.get("x", "")
                                if p_val > 0 and x_dt:
                                    date_str = str(x_dt).split("T")[0].split()[0]
                                    clean_history.append({"date": date_str, "price": p_val})
                            except Exception:
                                continue
                                
                        day_map = {}
                        for pt in clean_history:
                            d = pt["date"]
                            if d not in day_map or pt["price"] < day_map[d]["price"]:
                                day_map[d] = pt
                        sorted_history = sorted(day_map.values(), key=lambda x: x["date"])
                        
                        min_p = float(data.get("Price", {}).get("MinPrice") or 0) or None
                        mrp = float(data.get("Price", {}).get("MRP") or 0) or None
                        
                        return {
                            "source": "pricehistory_app",
                            "points": sorted_history,
                            "lowest_price": min_p,
                            "mrp": mrp,
                            "is_brand_new": len(sorted_history) <= 1
                        }
    except Exception as e:
        logger.debug(f"pricehistory.app fetch failed for {url}: {e}")
    return None


async def pull_and_sync_price_history(
    product_id: int,
    product_url: str,
    platform: str,
    current_price: float,
    created_at: Optional[datetime.datetime] = None,
    db_execute_func = None
) -> Dict[str, Any]:
    """
    Unified multi-store price history orchestrator:
    1. Checks external verified sources within a strict 2.5s window.
    2. Detects brand-new products truthfully.
    3. If dense history is obtained (>= 3 points), seeds into Supabase `daily_prices` in the background.
    """
    clean_url = canonicalize_product_url(product_url, platform)
    plat = (platform or "").lower()
    
    # 1. Check if product is brand new based on creation date & catalog status
    now = datetime.datetime.now(datetime.timezone.utc)
    is_recent_creation = False
    if created_at:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=datetime.timezone.utc)
        is_recent_creation = (now - created_at).total_seconds() < (14 * 86400) # within 14 days

    result_points: List[Dict[str, Any]] = []
    lowest_observed: Optional[float] = None
    mrp_observed: Optional[float] = None
    is_brand_new: bool = False
    source_used: str = "local"

    if clean_url:
        try:
            async with AsyncSession(impersonate="chrome120") as session:
                # For Amazon and Flipkart, try pricehistory.app or pricediff.in
                if "amazon" in plat or "flipkart" in plat:
                    # Run concurrently for fastest response
                    t_diff = fetch_from_pricediff(session, clean_url)
                    t_hist = fetch_from_pricehistory_app(session, clean_url)
                    done, _ = await asyncio.wait([asyncio.create_task(t_diff), asyncio.create_task(t_hist)], timeout=2.5)
                    
                    results = [t.result() for t in done if t.result()]
                    # Prefer the source with dense points
                    best = max(results, key=lambda r: len(r.get("points", [])), default=None)
                    if best:
                        result_points = best.get("points", [])
                        lowest_observed = best.get("lowest_price")
                        mrp_observed = best.get("mrp")
                        source_used = best.get("source", "external")
                        if best.get("is_brand_new") and is_recent_creation:
                            is_brand_new = True
                else:
                    # Myntra / Ajio / Fashion
                    res = await fetch_from_pricediff(session, clean_url)
                    if res:
                        result_points = res.get("points", [])
                        lowest_observed = res.get("lowest_price")
                        mrp_observed = res.get("mrp")
                        source_used = res.get("source", "pricediff")
                        if res.get("is_brand_new") and is_recent_creation:
                            is_brand_new = True
        except Exception as e:
            logger.warning(f"Error fetching external price history for product {product_id}: {e}")

    # Fallback brand new check: If no historical records exist and product was added within 14 days
    if len(result_points) <= 1 and is_recent_creation:
        is_brand_new = True

    # 2. Asynchronously seed retrieved points into Supabase `daily_prices` so future loads are instant
    if len(result_points) >= 2 and db_execute_func and product_id > 0:
        asyncio.create_task(
            _seed_daily_prices(product_id, result_points, lowest_observed, current_price, db_execute_func)
        )

    return {
        "status": "success",
        "product_id": product_id,
        "points": result_points,
        "count": len(result_points),
        "has_dense_history": len(result_points) >= 2,
        "is_brand_new": is_brand_new and len(result_points) <= 1,
        "lowest_price": lowest_observed,
        "mrp": mrp_observed,
        "source": source_used
    }


async def _seed_daily_prices(
    product_id: int,
    points: List[Dict[str, Any]],
    lowest_observed: Optional[float],
    current_price: float,
    db_execute_func
):
    """Background task to seed historical points into daily_prices and update products statistics."""
    try:
        from budgetby import database
        
        # Batch insert recent observations in one roundtrip using executemany (up to 180 points)
        recent_points = points[-180:]
        records = []
        for pt in recent_points:
            d_str = pt["date"]
            p_val = pt["price"]
            try:
                d_obj = datetime.date.fromisoformat(d_str.split()[0].split("T")[0])
                records.append((product_id, d_obj, p_val, p_val))
            except Exception:
                continue

        if records:
            pool = database.get_pool()
            async with pool.acquire() as conn:
                await conn.executemany("""
                    INSERT INTO daily_prices (product_id, date, min_price, close_price)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (product_id, date) DO UPDATE
                    SET min_price = LEAST(daily_prices.min_price, EXCLUDED.min_price),
                        close_price = EXCLUDED.close_price;
                """, records)

        # Calculate lowest price across all points
        all_prices = [p["price"] for p in points if p.get("price")]
        if current_price:
            all_prices.append(current_price)
        calc_atl = min(all_prices) if all_prices else current_price
        
        # Calculate 30-day lowest
        cutoff_30d = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
        prices_30d = [p["price"] for p in points if p.get("date", "") >= cutoff_30d]
        if current_price:
            prices_30d.append(current_price)
        calc_30d = min(prices_30d) if prices_30d else calc_atl

        await database.execute("""
            UPDATE products
            SET all_time_low = LEAST(COALESCE(all_time_low, $2), $2),
                min_30d = LEAST(COALESCE(min_30d, $3), $3)
            WHERE id = $1;
        """, product_id, calc_atl, calc_30d)

        # Invalidate RAM cache
        try:
            from budgetby.dashboard.app import ram_cache
            await ram_cache.delete(f"deal_detail:{product_id}")
            await ram_cache.delete(f"ssr_html:deal_detail:{product_id}")
            await ram_cache.delete(f"ssr:deal_detail:{product_id}")
        except Exception as e:
            logger.debug(f"Cache invalidation note: {e}")
        logger.info(f"Successfully seeded {len(recent_points)} history points for product {product_id}")
    except Exception as e:
        logger.error(f"Error seeding daily_prices for product {product_id}: {e}")
