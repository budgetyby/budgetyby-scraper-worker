"""
BudgetBy — Deal Scorer (Separated Shopper Quality & Monetization)
Calculates:
1. Shopper Deal Quality Score (0–100): Pure consumer deal value (savings magnitude, historical low position, popularity/rating).
2. Monetization Score (0–100): Internal commercial value (commission rate, estimated ₹ payout, ticket size).
"""

import logging
from typing import Union, Dict, Any
from budgetby import config

logger = logging.getLogger("budgetby.engine.deal_scorer")


def calculate_shopper_score(product: Union[Dict[str, Any], Any], detection_result: Dict[str, Any]) -> float:
    """
    Calculates the Shopper Deal Quality Score (0–100).
    Answers: 'How good is this deal for a consumer?'
    ZERO commission bias.
    
    Signals & Weights:
    - 40% Drop / Savings Magnitude (savings percentage from MRP)
    - 30% Historical Price Position (proximity to ATL, near_ATL, 90d, 60d, 30d low)
    - 30% Popularity & Social Proof (15% review volume + 15% star rating)
    - Penalty: -20pts if fake discount detected
    """
    try:
        if not product or not detection_result or not isinstance(detection_result, dict):
            return 0.0

        weights = getattr(config, "SHOPPER_SCORE_WEIGHTS", {
            "drop_magnitude": 0.40,
            "historical_position": 0.30,
            "popularity": 0.30,
        })

        # 1. Drop / Savings Magnitude (40%)
        savings_pct = float(detection_result.get("savings_pct", 0.0) or 0.0)
        pct_val = savings_pct * 100.0
        if pct_val >= 50.0:
            drop_score = 100.0
        elif pct_val >= 20.0:
            drop_score = 60.0 + ((pct_val - 20.0) / 30.0) * 40.0
        elif pct_val >= 5.0:
            drop_score = 20.0 + ((pct_val - 5.0) / 15.0) * 40.0
        else:
            drop_score = (pct_val / 5.0) * 20.0
        drop_score = max(0.0, min(100.0, drop_score))

        # 2. Historical Price Position (30%)
        badge = str(detection_result.get("badge") or "").strip()
        if badge in ("ATL", "LOOT"):
            hist_score = 100.0
        elif badge == "near_ATL":
            hist_score = 80.0
        elif badge == "90d_low":
            hist_score = 70.0
        elif badge == "60d_low":
            hist_score = 50.0
        elif badge == "30d_low":
            hist_score = 40.0
        else:
            hist_score = 20.0

        # 3. Popularity & Social Proof (30% total = 15% reviews + 15% rating)
        review_count = int(product.get("review_count", 0) or 0)
        if review_count >= 50000:
            review_score = 100.0
        elif review_count >= 10000:
            review_score = 90.0 + ((review_count - 10000) / 40000) * 10.0
        elif review_count >= 5000:
            review_score = 70.0 + ((review_count - 5000) / 5000) * 20.0
        elif review_count >= 1000:
            review_score = 50.0 + ((review_count - 1000) / 4000) * 20.0
        elif review_count >= 100:
            review_score = 30.0 + ((review_count - 100) / 900) * 20.0
        elif review_count > 0:
            review_score = 10.0 + (review_count / 100) * 20.0
        else:
            review_score = 25.0  # Neutral fallback for missing reviews

        rating = float(product.get("rating", 0.0) or 0.0)
        if rating >= 4.5:
            rating_score = 100.0
        elif rating >= 4.0:
            rating_score = 80.0 + ((rating - 4.0) / 0.5) * 20.0
        elif rating >= 3.5:
            rating_score = 60.0 + ((rating - 3.5) / 0.5) * 20.0
        elif rating >= 3.0:
            rating_score = 40.0 + ((rating - 3.0) / 0.5) * 20.0
        elif rating > 0.0:
            rating_score = 20.0 + (rating / 3.0) * 20.0
        else:
            rating_score = 50.0  # Neutral fallback for missing rating

        popularity_score = (review_score * 0.50) + (rating_score * 0.50)

        total_score = (
            (drop_score * weights.get("drop_magnitude", 0.40)) +
            (hist_score * weights.get("historical_position", 0.30)) +
            (popularity_score * weights.get("popularity", 0.30))
        )

        # 4. Penalty for fake discount
        if detection_result.get("is_fake_discount"):
            penalty = getattr(config, "FAKE_DISCOUNT_PENALTY", 20.0)
            total_score -= penalty

        return round(max(0.0, min(100.0, total_score)), 1)
    except Exception as e:
        logger.error(f"Error in calculate_shopper_score: {e}")
        return 0.0


def calculate_monetization_score(product: Union[Dict[str, Any], Any], detection_result: Dict[str, Any] = None) -> float:
    """
    Calculates the Monetization Score (0–100).
    Answers: 'How commercially valuable is this deal to BudgetBy?'
    Internal business metric.
    
    Signals & Weights:
    - 30% Affiliate Commission Rate (% of sale)
    - 50% Estimated ₹ Payout per sale (current_price * comm_rate)
    - 20% Ticket Volume Tier (GMV value)
    """
    try:
        if not product:
            return 0.0

        current_price = float(product.get("current_price", 0.0) or 0.0)
        category = str(product.get("category", "default") or "default").lower()
        platform = str(product.get("platform", "amazon") or "amazon").lower()

        # 1. Affiliate Commission Rate (30%)
        comm_rate = 0.05
        if platform == "amazon":
            comm_rate = config.AMAZON_COMMISSION_RATES.get(category, 0.045)
        elif platform in getattr(config, "EARNKARO_COMMISSION_RATES", {}):
            comm_rate = config.EARNKARO_COMMISSION_RATES[platform].get(category, 0.03)

        rate_score = min(100.0, comm_rate * 1000.0)

        # 2. Estimated Commission Payout (50%)
        estimated_commission = current_price * comm_rate
        # ₹200+ payout = 100 points
        payout_score = min(100.0, (estimated_commission / 200.0) * 100.0)

        # 3. Ticket Size Tier (20%)
        if current_price >= 10000.0:
            ticket_score = 100.0
        elif current_price >= 3000.0:
            ticket_score = 75.0
        elif current_price >= 1000.0:
            ticket_score = 50.0
        elif current_price > 0.0:
            ticket_score = 25.0
        else:
            ticket_score = 0.0

        weights = getattr(config, "MONETIZATION_SCORE_WEIGHTS", {
            "commission_rate": 0.30,
            "commission_payout": 0.50,
            "price_tier": 0.20,
        })

        total_score = (
            (rate_score * weights.get("commission_rate", 0.30)) +
            (payout_score * weights.get("commission_payout", 0.50)) +
            (ticket_score * weights.get("price_tier", 0.20))
        )

        return round(max(0.0, min(100.0, total_score)), 1)
    except Exception as e:
        logger.error(f"Error in calculate_monetization_score: {e}")
        return 0.0


def score_deal(product: Union[Dict[str, Any], Any], detection_result: Dict[str, Any]) -> float:
    """
    Standard entry point: returns Shopper Deal Quality Score (0–100).
    Preserves backward compatibility across all engine pipelines and tests.
    """
    return calculate_shopper_score(product, detection_result)


def score_deal_full(product: Union[Dict[str, Any], Any], detection_result: Dict[str, Any]) -> Dict[str, float]:
    """
    Returns both scores cleanly in a structured dictionary.
    """
    shopper_score = calculate_shopper_score(product, detection_result)
    monetization_score = calculate_monetization_score(product, detection_result)
    return {
        "shopper_quality_score": shopper_score,
        "monetization_score": monetization_score,
        "deal_score": shopper_score  # Backward compatibility alias
    }
