"""
BudgetBy — Local Discovery Telemetry & Intelligence Engine
Tracks discovery funnel metrics in-memory locally with ZERO Supabase egress:
- Products discovered
- Products successfully scraped
- Price movements detected
- Real deals detected
- Fake discounts blocked
- Cooldown skips
- Deals posted to Telegram
"""

import logging
from typing import Dict, Any

logger = logging.getLogger("budgetby.engine.discovery_telemetry")

_telemetry_data: Dict[str, Dict[str, int]] = {}


def record_discovery_event(source: str, event_type: str, count: int = 1):
    """
    Records a local discovery event for telemetry and source-quality scoring.
    Zero cloud reads / writes.
    """
    global _telemetry_data
    src_key = (source or "unknown").lower().strip()
    if src_key not in _telemetry_data:
        _telemetry_data[src_key] = {
            "discovered": 0,
            "scraped": 0,
            "price_changed": 0,
            "deal_detected": 0,
            "fake_discount": 0,
            "cooldown_skipped": 0,
            "posted": 0
        }
    
    evt_key = event_type.lower().strip()
    if evt_key in _telemetry_data[src_key]:
        _telemetry_data[src_key][evt_key] += count
    else:
        _telemetry_data[src_key][evt_key] = count


def get_source_metrics(source: str = None) -> Dict[str, Any]:
    """
    Returns telemetry metrics for a single source or all sources.
    """
    global _telemetry_data
    if source:
        src_key = source.lower().strip()
        metrics = _telemetry_data.get(src_key, {
            "discovered": 0, "scraped": 0, "price_changed": 0,
            "deal_detected": 0, "fake_discount": 0, "cooldown_skipped": 0, "posted": 0
        }).copy()
        scraped = max(1, metrics["scraped"])
        discovered = max(1, metrics["discovered"])
        metrics["valid_deal_rate_pct"] = round((metrics["deal_detected"] / scraped) * 100, 1)
        metrics["post_conversion_rate_pct"] = round((metrics["posted"] / discovered) * 100, 1)
        return metrics

    summary = {}
    for src, m in _telemetry_data.items():
        summary[src] = m.copy()
        scraped = max(1, m["scraped"])
        discovered = max(1, m["discovered"])
        summary[src]["valid_deal_rate_pct"] = round((m["deal_detected"] / scraped) * 100, 1)
        summary[src]["post_conversion_rate_pct"] = round((m["posted"] / discovered) * 100, 1)
    return summary


def reset_telemetry():
    """Resets local in-memory telemetry data (used in unit testing)."""
    global _telemetry_data
    _telemetry_data = {}
