import asyncio
import random
from decimal import Decimal
from typing import List
from .base import ExchangeBase, Order, Position

class MockExchange(ExchangeBase):
    def __init__(self, name: str = "MockExchange"):
        super().__init__(name)
        self.positions: dict[str, Position] = {} # symbol -> Position
        self.balance = Decimal("10000.00")
        self.current_price = Decimal("100.00")

    async def connect(self):
        await asyncio.sleep(0.5) # Simulate network delay
        self.connected = True
        return True

    async def get_balance(self, asset: str = "USDC") -> Decimal:
        return self.balance

    async def get_price(self, symbol: str) -> Decimal:
        # Simulate price fluctuation
        change = Decimal(random.uniform(-0.5, 0.5))
        self.current_price += change
        return self.current_price

    async def open_position(self, symbol: str, side: str, amount: Decimal) -> Order:
        if not self.connected:
            raise ConnectionError("Exchange not connected")
        
        price = await self.get_price(symbol)
        
        # Simple simulation: just track position
        pos = Position(
            symbol=symbol,
            side=side,
            size=amount,
            entry_price=price,
            unrealized_pnl=Decimal("0.0")
        )
        self.positions[symbol] = pos
        return Order(symbol=symbol, side=side, amount=amount, price=price, order_type='market')

    async def close_position(self, symbol: str) -> Order:
        if symbol in self.positions:
            del self.positions[symbol]
            return Order(symbol=symbol, side='close', amount=Decimal("0"), price=self.current_price, order_type='market')
        return None

    async def get_positions(self) -> List[Position]:
        # Update unrealized PnL for mock positions
        current_p = await self.get_price("BTC-PERP")
        for symbol, pos in self.positions.items():
            diff = current_p - pos.entry_price
            if pos.side == 'short':
                diff = -diff
            pos.unrealized_pnl = diff * pos.size
        return list(self.positions.values())
