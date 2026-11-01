"""
ProductSeeder orchestrator with customized high-profit proportions (~75,000 products):
- Amazon (31%): ~23,250 products (Official Tag: dealpulse21-21)
- Flipkart (25%): ~18,750 products (EarnKaro: r=5549565)
- Myntra (20%): ~15,000 products (EarnKaro: r=5549565)
- Ajio (15%): ~11,250 products (EarnKaro: r=5549565)
- Nykaa (9%): ~6,750 products (EarnKaro: r=5549565)
"""
import logging
import asyncio
from budgetby import config
from budgetby import database
from budgetby.discovery import amazon_discover, flipkart_discover, ajio_discover

logger = logging.getLogger("budgetby.discovery.seeder")

TARGET_PROPORTIONS = {
    "amazon": 24000,    # ~30%
    "flipkart": 24000,  # ~30%
    "myntra": 16000,    # ~20%
    "ajio": 16000,      # ~20%
    "nykaa": 8000,      # ~10%
}

class ProductSeeder:
    def __init__(self):
        self.stats = {
            "new_products_added": 0,
            "existing_updated": 0,
            "errors": 0
        }

    async def get_platform_count(self, platform: str) -> int:
        try:
            return await database.fetchval("SELECT COUNT(*) FROM products WHERE platform = $1", platform) or 0
        except Exception:
            return 0

    async def seed_amazon(self):
        current_count = await self.get_platform_count("amazon")
        target = TARGET_PROPORTIONS["amazon"]
        if current_count >= target:
            logger.info(f"Amazon target reached ({current_count}/{target}). Skipping discovery.")
            return

        logger.info(f"Starting throttled Amazon discovery ({current_count}/{target} target)...")
        for category, info in config.AMAZON_DISCOVERY_TARGETS.items():
            current_count = await self.get_platform_count("amazon")
            if current_count >= target:
                break
            try:
                slug = info.get("slug", category)
                
                # Bestsellers (single page, slowed down)
                products = await amazon_discover.discover_bestsellers(slug, pages=1)
                for p in products:
                    p["category"] = info.get("category", category)
                await self._upsert_batch(products)
                await asyncio.sleep(3.0)

                # New Releases (single page, slowed down)
                new_rel = await amazon_discover.discover_new_releases(slug, pages=1)
                for p in new_rel:
                    p["category"] = info.get("category", category)
                await self._upsert_batch(new_rel)
                await asyncio.sleep(3.0)

                # Search Keywords (throttled to max 2 keywords with 4s pause)
                keywords = amazon_discover.AMAZON_CATEGORY_KEYWORDS.get(category, [])
                for kw in keywords[:2]:
                    kw_prods = await amazon_discover.discover_search_keywords(kw, pages=1)
                    for p in kw_prods:
                        p["category"] = info.get("category", category)
                    await self._upsert_batch(kw_prods)
                    await asyncio.sleep(4.0)

                await asyncio.sleep(5.0)
            except Exception as e:
                logger.error(f"Error seeding Amazon {category}: {e}")
                self.stats["errors"] += 1

    async def seed_flipkart(self, sort_mode: str = "popularity"):
        current_count = await self.get_platform_count("flipkart")
        target = TARGET_PROPORTIONS["flipkart"]
        if current_count >= target:
            logger.info(f"Flipkart target reached ({current_count}/{target}). Skipping deep wave.")
            return

        logger.info(f"Starting concurrent Flipkart discovery [sort={sort_mode}] ({current_count}/{target} target)...")
        sem = asyncio.Semaphore(4)

        async def process_category(category, info):
            cnt = await self.get_platform_count("flipkart")
            if cnt >= target:
                return
            async with sem:
                try:
                    sid = info.get("sid", "")
                    cat_type = info.get("category", "fashion")
                    products = await flipkart_discover.discover_category(category, sid, pages=12, sort=sort_mode)
                    for p in products:
                        p["category"] = cat_type
                        await self._upsert(p)
                    logger.info(f"Flipkart {category} ({sort_mode}): Added {len(products)} products")
                    await asyncio.sleep(0.2)
                except Exception as e:
                    logger.error(f"Error seeding Flipkart {category}: {e}")
                    self.stats["errors"] += 1

        tasks = [process_category(cat, info) for cat, info in config.FLIPKART_DISCOVERY_TARGETS.items()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def seed_myntra(self, sort_mode: str = "popularity"):
        logger.info("🛑 Myntra seeding is disabled.")
        return

    async def seed_ajio(self):
        current_count = await self.get_platform_count("ajio")
        target = TARGET_PROPORTIONS["ajio"]
        if current_count >= target:
            logger.info(f"Ajio target reached ({current_count}/{target}). Skipping deep wave.")
            return

        logger.info(f"Starting Ajio discovery ({current_count}/{target} target)...")
        for category, info in getattr(config, "AJIO_DISCOVERY_TARGETS", {}).items():
            current_count = await self.get_platform_count("ajio")
            if current_count >= target:
                break
            try:
                code = info.get("code", "")
                cat_type = info.get("category", "fashion")
                products = await ajio_discover.discover_category(code, pages=30)
                for p in products:
                    p["category"] = cat_type
                await self._upsert_batch(products)
                logger.info(f"Ajio {category}: Added {len(products)} products")
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error seeding Ajio {category}: {e}")
                self.stats["errors"] += 1

    async def seed_nykaa(self, sort_mode: str = "popularity"):
        # Explicitly disabled per user request to rely purely on live smart deal hunter
        return

    async def _upsert(self, product_data: dict):
        try:
            if not product_data.get("product_url"):
                return
            await database.upsert_product(product_data)
            self.stats["new_products_added"] += 1
        except Exception as e:
            logger.error(f"Error upserting product {product_data.get('platform_id')}: {e}")
            self.stats["errors"] += 1

    async def _upsert_batch(self, products: list[dict], batch_size: int = 25):
        """Batches product upserts in parallel chunks to minimize DB round-trips."""
        if not products:
            return
        valid_prods = [p for p in products if p.get("product_url")]
        for i in range(0, len(valid_prods), batch_size):
            chunk = valid_prods[i:i + batch_size]
            tasks = [database.upsert_product(p) for p in chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    self.stats["errors"] += 1
                else:
                    self.stats["new_products_added"] += 1

    async def _platform_worker(self, platform: str, seed_func, sort_modes: list = None):
        """Runs discovery for a single platform until its configured target is reached, then cleanly exits."""
        target = TARGET_PROPORTIONS.get(platform, 15000)
        sort_modes = sort_modes or ["popularity"]
        mode_idx = 0

        while True:
            try:
                current_count = await self.get_platform_count(platform)
                if current_count >= target:
                    logger.info(f"[{platform.upper()}] Target reached ({current_count}/{target}). Worker completed cleanly.")
                    return  # Target reached, exit worker!

                active_sort = sort_modes[mode_idx % len(sort_modes)]
                mode_idx += 1

                logger.info(f"[{platform.upper()}] Starting discovery pass [sort={active_sort}] ({current_count}/{target} target)...")
                if platform in ("myntra", "nykaa", "flipkart"):
                    await seed_func(sort_mode=active_sort)
                else:
                    await seed_func()
                logger.info(f"[{platform.upper()}] Discovery pass ({active_sort}) completed. Brief pause before next cycle.")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"[{platform.upper()}] Worker error: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def run_bootstrap_until_target(self, target_count: int = 75000):
        """
        Runs targeted bootstrap for platforms below quota.
        """
        logger.info("Initializing bootstrap discovery...")

        # 2. Nykaa Bootstrap (Temporarily paused per user request - relies on Live Hunter)

        logger.info("✨ Bootstrap seeder finished! New products will be dynamically ingested and saved to DB whenever deals arrive.")


    async def run_full_discovery(self) -> dict:
        """Runs standard multi-platform discovery pass across active platforms (Amazon, Flipkart, Ajio)."""
        logger.info("Running routine multi-platform discovery pass across Amazon, Flipkart, Ajio...")
        results = await asyncio.gather(
            self.seed_flipkart(),
            self.seed_ajio(),
            self.seed_amazon(),
            return_exceptions=True
        )
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Discovery task failed: {r}", exc_info=r)
        logger.info(f"Discovery pass complete. Stats: {self.stats}")
        return self.stats

