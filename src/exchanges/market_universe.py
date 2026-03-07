import asyncio
import logging
from dataclasses import dataclass
from typing import List

from .base import ExchangeBase

logger = logging.getLogger(__name__)


@dataclass
class MarketUniverse:
    exchange_a_assets: List[str]
    exchange_b_assets: List[str]
    common_assets: List[str]


async def build_common_market_universe(
    exchange_a: ExchangeBase,
    exchange_b: ExchangeBase,
) -> MarketUniverse:
    """
    Builds market universe for hedging.
    common_assets = intersection of available underlyings on both DEX.
    """
    results = await asyncio.gather(
        exchange_a.get_markets(),
        exchange_b.get_markets(),
        return_exceptions=True,
    )

    raw_a, raw_b = results
    if isinstance(raw_a, Exception):
        logger.error(f"Failed to fetch markets from {exchange_a.name}: {raw_a}")
        list_a: List[str] = []
    else:
        list_a = sorted(set(raw_a))

    if isinstance(raw_b, Exception):
        logger.error(f"Failed to fetch markets from {exchange_b.name}: {raw_b}")
        list_b: List[str] = []
    else:
        list_b = sorted(set(raw_b))

    common = sorted(set(list_a).intersection(list_b))
    return MarketUniverse(exchange_a_assets=list_a, exchange_b_assets=list_b, common_assets=common)
