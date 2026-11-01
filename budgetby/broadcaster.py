"""
BudgetBy — 24/7 Continuous 30-Second Paced Deal Broadcaster.
Runs continuously inside Render Web Service background loop:
1. Every 30 seconds:
   - Fetches the next verified active deal or hot catalog discount from Supabase.
   - Converts to official affiliate shortlink (Amazon, EarnKaro fktr/ajiio/myntr, Cuelinks clnk.in).
   - Posts deal with photo to Telegram channel @deal_pulse_alerts.
   - Sleeps for 30 seconds.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from curl_cffi.requests import AsyncSession
from budgetby import config, database
from budgetby.bot.templates import format_mega_deal, format_today_deal
from budgetby.cloud_scraper import get_or_create_affiliate_link

logger = logging.getLogger("budgetby.cloud_broadcaster")

_last_posted_product_ids = set()

broadcaster_stats = {
    "status": "starting",
    "total_posted": 0,
    "last_posted_product_id": None,
    "last_posted_title": None,
    "last_posted_at": None,
    "channel": getattr(config, "TELEGRAM_CHANNEL_ID", "@deal_pulse_alerts")
}

def get_broadcaster_stats() -> dict:
    """Return live status of the 30-second broadcaster."""
    return {
        **broadcaster_stats,
        "history_size": len(_last_posted_product_ids),
        "channel": getattr(config, "TELEGRAM_CHANNEL_ID", "@deal_pulse_alerts"),
        "now": datetime.now(timezone.utc).isoformat()
    }

async def run_paced_30s_broadcaster():
    """Continuous 30-second loop posting deals to Telegram & ensuring live website deals."""
    logger.info("🚀 [30s CLOUD BROADCASTER] Initializing continuous 30-second deal poster...")
    broadcaster_stats["status"] = "running"
    await asyncio.sleep(5)  # Brief warm-up delay on server boot

    while True:
        try:
            bot_token = getattr(config, "TELEGRAM_BOT_TOKEN", "").strip() or "8507632294:AAH8FmrM22hqvkk0A9ng97xs8DOod0j2Tng"
            channel_id = getattr(config, "TELEGRAM_CHANNEL_ID", "").strip() or "@deal_pulse_alerts"

            if database._pool is None:
                await database.init_pool()

            # 1. Fetch next candidate deal using primary key index
            exclude_clause = ""
            if _last_posted_product_ids:
                recent_list = list(_last_posted_product_ids)[-250:]
                exclude_clause = f"AND d.product_id NOT IN ({','.join(str(i) for i in recent_list)})"

            query = f"""
                SELECT d.id as deal_id, d.product_id, d.posted_price, d.posted_mrp, 
                       d.deal_score, d.badge, d.posted_at,
                       p.title, p.platform, p.product_url, p.affiliate_url, p.image_url, 
                       p.current_price, p.mrp, p.category, p.rating
                FROM deals d
                JOIN products p ON d.product_id = p.id
                WHERE p.in_stock = TRUE AND p.status = 'ACTIVE'
                  {exclude_clause}
                ORDER BY d.id DESC
                LIMIT 1;
            """
            deal_row = await database.fetchrow(query)

            # Fallback to high-discount catalog item if no unposted deal found
            if not deal_row:
                deal_row = await database.fetchrow(f"""
                    SELECT 0 as deal_id, p.id as product_id, p.current_price as posted_price, p.mrp as posted_mrp,
                           ROUND((((p.mrp - p.current_price)/NULLIF(p.mrp,0)) * 100)::numeric, 1) as deal_score,
                           'HOT_DEAL' as badge, NOW() as posted_at,
                           p.title, p.platform, p.product_url, p.affiliate_url, p.image_url, 
                           p.current_price, p.mrp, p.category, p.rating
                    FROM products p
                    WHERE p.in_stock = TRUE AND p.status = 'ACTIVE' AND p.current_price > 0
                      AND p.mrp > p.current_price
                      AND ((p.mrp - p.current_price) / p.mrp) >= 0.35
                      {exclude_clause.replace('d.product_id', 'p.id')}
                    ORDER BY p.id DESC
                    LIMIT 1;
                """)

            if deal_row:
                pid = deal_row["product_id"]
                _last_posted_product_ids.add(pid)
                if len(_last_posted_product_ids) > 600:
                    # Pop oldest items
                    first_val = next(iter(_last_posted_product_ids))
                    _last_posted_product_ids.remove(first_val)

                prod_dict = dict(deal_row)
                prod_dict["id"] = pid

                # 100% STRICT AFFILIATE CONVERSION ENFORCEMENT
                # Never post raw merchant links. Only post official verified affiliate tracking shortlinks.
                from budgetby.cloud_scraper import is_valid_affiliate_shortlink
                aff_url = await get_or_create_affiliate_link(prod_dict)
                if not aff_url or not is_valid_affiliate_shortlink(aff_url, prod_dict.get("platform")):
                    logger.warning(
                        f"⚠️ [AFFILIATE GUARD] Product {pid} ({prod_dict.get('platform')}) has unconverted link. "
                        f"Skipping post to guarantee 100% affiliate compliance."
                    )
                    await asyncio.sleep(2)
                    continue

                prod_dict["affiliate_url"] = aff_url

                # Format message
                score = float(deal_row.get("deal_score") or 50.0)
                badge = deal_row.get("badge") or "HOT_DEAL"
                deal_info = {
                    "deal_score": score,
                    "badge": badge,
                    "posted_price": float(deal_row["posted_price"] or 0),
                    "posted_mrp": float(deal_row["posted_mrp"] or 0)
                }

                if score >= 70 or badge in ("ATL", "LOOT"):
                    caption = format_mega_deal(prod_dict, deal_info)
                else:
                    caption = format_today_deal(prod_dict, deal_info)

                image_url = prod_dict.get("image_url")
                api_base = f"https://api.telegram.org/bot{bot_token}"

                posted = False
                message_id = None

                # Attempt 1: Fetch and convert photo to clean JPEG (handles AVIF/WebP formats that Telegram rejects)
                if image_url and image_url.startswith("http"):
                    try:
                        import httpx, io
                        from PIL import Image
                        async with AsyncSession(impersonate="chrome124", timeout=8) as dl_sess:
                            r_img = await dl_sess.get(image_url)
                        if r_img.status_code == 200 and len(r_img.content) > 1000:
                            im = Image.open(io.BytesIO(r_img.content)).convert("RGB")
                            buf = io.BytesIO()
                            im.save(buf, format="JPEG", quality=88)
                            buf.seek(0)
                            async with httpx.AsyncClient(timeout=15) as tg_client:
                                files = {"photo": ("product.jpg", buf.getvalue(), "image/jpeg")}
                                data = {"chat_id": channel_id, "caption": caption, "parse_mode": "HTML"}
                                tg_resp = await tg_client.post(f"{api_base}/sendPhoto", data=data, files=files)
                                if tg_resp.status_code == 200:
                                    posted = True
                                    try:
                                        message_id = tg_resp.json().get("result", {}).get("message_id")
                                    except Exception:
                                        pass
                    except Exception as img_err:
                        logger.debug(f"Photo upload fallback to sendPhoto URL / text: {img_err}")

                # Attempt 2: Fallback to direct URL sendPhoto
                if not posted and image_url and image_url.startswith("http"):
                    async with AsyncSession(timeout=10) as session:
                        resp = await session.post(f"{api_base}/sendPhoto", json={
                            "chat_id": channel_id,
                            "photo": image_url,
                            "caption": caption,
                            "parse_mode": "HTML"
                        })
                        if resp.status_code == 200:
                            posted = True
                            try:
                                message_id = resp.json().get("result", {}).get("message_id")
                            except Exception:
                                pass

                # Attempt 3: Fallback to sendMessage (text alert with web preview)
                if not posted:
                    async with AsyncSession(timeout=10) as session:
                        resp = await session.post(f"{api_base}/sendMessage", json={
                            "chat_id": channel_id,
                            "text": caption,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": False
                        })
                        if resp.status_code == 200:
                            posted = True
                            try:
                                message_id = resp.json().get("result", {}).get("message_id")
                            except Exception:
                                pass

                if posted:
                    broadcaster_stats["total_posted"] += 1
                    broadcaster_stats["last_posted_product_id"] = pid
                    broadcaster_stats["last_posted_title"] = prod_dict.get("title", "")[:50]
                    broadcaster_stats["last_posted_at"] = datetime.now(timezone.utc).isoformat()
                    logger.info(f"📢 [30s CLOUD POSTED #{broadcaster_stats['total_posted']}] Deal {pid}: {prod_dict.get('title', '')[:30]} ➔ {channel_id}")

                    # 1. Synchronize deal & product tables with current timestamp & price drop for website storefront
                    posted_price = float(deal_row.get("posted_price") or prod_dict.get("current_price") or 0)
                    posted_mrp = float(deal_row.get("posted_mrp") or prod_dict.get("mrp") or posted_price)
                    savings = max(0.0, posted_mrp - posted_price)
                    savings_pct = (savings / posted_mrp * 100) if posted_mrp > 0 else 0.0

                    try:
                        await database.execute("""
                            INSERT INTO deals (
                                product_id, deal_type, posted_price, posted_mrp,
                                savings_amount, savings_pct, deal_score, badge,
                                source_channel, posted_at
                            ) VALUES (
                                $1, 'price_drop', $2, $3, $4, $5, $6, $7, 'cloud_broadcaster', NOW()
                            )
                            ON CONFLICT (product_id) DO UPDATE SET
                                posted_price = EXCLUDED.posted_price,
                                posted_mrp = EXCLUDED.posted_mrp,
                                savings_amount = EXCLUDED.savings_amount,
                                savings_pct = EXCLUDED.savings_pct,
                                deal_score = EXCLUDED.deal_score,
                                badge = EXCLUDED.badge,
                                deal_type = 'price_drop',
                                posted_at = NOW();
                        """, pid, posted_price, posted_mrp, savings, savings_pct, score, badge)

                        await database.execute("""
                            UPDATE products 
                            SET previous_price = GREATEST(COALESCE(previous_price, mrp, current_price), current_price * 1.15),
                                last_price_change = NOW(),
                                last_checked = NOW(),
                                affiliate_url = COALESCE($2, affiliate_url)
                            WHERE id = $1;
                        """, pid, aff_url)

                        # Invalidate website SSR & RAM cache so deal appears in Just Dropped immediately
                        try:
                            from budgetby.dashboard.app import invalidate_ssr_cache, ram_cache
                            await invalidate_ssr_cache()
                            await ram_cache.clear()
                        except Exception:
                            pass
                    except Exception as db_sync_err:
                        logger.warning(f"Error synchronizing deal/product table for website: {db_sync_err}")

                    # 2. Record tracking if message_id exists
                    if message_id:
                        try:
                            await database.insert_deal_tracking(
                                deal_id=deal_row.get("deal_id") or 0,
                                product_id=pid,
                                message_id=message_id,
                                channel_id=channel_id,
                                caption=caption,
                                price=float(deal_row["posted_price"] or 0)
                            )
                        except Exception as tr_err:
                            logger.debug(f"Tracking insert notice: {tr_err}")
                else:
                    logger.warning(f"Failed to post deal {pid} to Telegram")

        except Exception as e:
            logger.error(f"Error in run_paced_30s_broadcaster loop: {e}")

        # Sleep exactly 30 seconds between every single post
        await asyncio.sleep(30)
