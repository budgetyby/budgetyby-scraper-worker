"""
BudgetBy — Scraper Base Class
Defines the abstract interface for all platform scrapers.
"""
import asyncio
import random
import logging
from abc import ABC, abstractmethod
from typing import Callable, Any

from budgetby import config

class BaseScraper(ABC):
    def __init__(self):
        self.logger = logging.getLogger(f"budgetby.scrapers.{self.__class__.__name__}")

    async def _delay(self):
        """Built-in random delay before requests."""
        delay = random.uniform(config.SCRAPER_DELAY_MIN, config.SCRAPER_DELAY_MAX)
        await asyncio.sleep(delay)

    async def _with_retry(self, func: Callable, *args, **kwargs) -> Any:
        """Built-in retry logic with exponential backoff."""
        max_retries = config.SCRAPER_MAX_RETRIES
        for attempt in range(max_retries):
            try:
                await self._delay()
                return await func(*args, **kwargs)
            except Exception as e:
                self.logger.warning(f"Scrape failed (attempt {attempt+1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise
                backoff = config.SCRAPER_RETRY_BACKOFF_BASE * (2 ** attempt)
                await asyncio.sleep(backoff)

    async def scrape_product(self, url: str) -> dict:
        return await self._with_retry(self._do_scrape_product, url)
        
    async def scrape_listing(self, url: str) -> list[dict]:
        return await self._with_retry(self._do_scrape_listing, url)

    @abstractmethod
    async def _do_scrape_product(self, url: str) -> dict:
        """
        Scrape product details from URL.
        Returns dict with: title, current_price, mrp, rating, review_count, 
        in_stock, image_url, brand, category, is_renewed
        """
        pass

    @abstractmethod
    async def _do_scrape_listing(self, url: str) -> list[dict]:
        """
        Scrape a listing/category page.
        Returns list of dicts (platform_id, product_url, title)
        """
        pass
