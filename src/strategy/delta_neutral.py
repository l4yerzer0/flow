import asyncio
import random
import logging
from decimal import Decimal
from typing import Optional
from enum import Enum
from ..exchanges.base import ExchangeBase

class StrategyState(Enum):
    IDLE = "IDLE"
    OPENING = "OPENING"
    HEDGED = "HEDGED"
    CLOSING = "CLOSING"

class DeltaNeutralStrategy:
    def __init__(self, exchange_a: ExchangeBase, exchange_b: ExchangeBase):
        self.ex_a = exchange_a
        self.ex_b = exchange_b
        self.state = StrategyState.IDLE
        self.target_size_usd = Decimal("1000.00")  # Position size per leg
        self.symbol = "BTC-PERP"
        self.logger = logging.getLogger("strategy")
        self.current_pnl = Decimal("0.0")

    async def run_loop(self):
        """Main strategy loop."""
        while True:
            try:
                if self.state == StrategyState.IDLE:
                    await self._handle_idle()
                elif self.state == StrategyState.OPENING:
                    await self._handle_opening()
                elif self.state == StrategyState.HEDGED:
                    await self._handle_hedged()
                elif self.state == StrategyState.CLOSING:
                    await self._handle_closing()
            except Exception as e:
                self.logger.error(f"Strategy Error: {e}")
                await asyncio.sleep(5)
            
            await asyncio.sleep(1)

    async def _handle_idle(self):
        # Simulate analyzing market or waiting for cooldown
        self.logger.info("Analyzing market...")
        await asyncio.sleep(random.uniform(2, 5))
        self.state = StrategyState.OPENING

    async def _handle_opening(self):
        self.logger.info(f"Opening positions for {self.target_size_usd} USD...")
        
        # Get prices
        price_a = await self.ex_a.get_price(self.symbol)
        price_b = await self.ex_b.get_price(self.symbol)
        
        # Calculate amounts
        amount_a = self.target_size_usd / price_a
        amount_b = self.target_size_usd / price_b
        
        # Execute (In real life, we'd use gather or sequential depending on risk)
        await self.ex_a.open_position(self.symbol, 'buy', amount_a)
        await self.ex_b.open_position(self.symbol, 'sell', amount_b)
        
        self.state = StrategyState.HEDGED
        self.logger.info("Positions OPENED. Hedged.")

    async def _handle_hedged(self):
        # Hold for random duration
        duration = random.uniform(5, 10)
        self.logger.info(f"Holding positions for {duration:.1f}s...")
        
        # Monitor PnL
        for _ in range(int(duration)):
            pos_a = (await self.ex_a.get_positions())[0] # Assuming single pos
            pos_b = (await self.ex_b.get_positions())[0]
            self.current_pnl = pos_a.unrealized_pnl + pos_b.unrealized_pnl
            await asyncio.sleep(1)
            
        self.state = StrategyState.CLOSING

    async def _handle_closing(self):
        self.logger.info("Closing all positions...")
        await self.ex_a.close_position(self.symbol)
        await self.ex_b.close_position(self.symbol)
        
        self.current_pnl = Decimal("0.0")
        self.state = StrategyState.IDLE
        self.logger.info("Positions CLOSED. Returning to IDLE.")
