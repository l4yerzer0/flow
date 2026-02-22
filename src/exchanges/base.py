from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from decimal import Decimal
from pydantic import BaseModel

class Order(BaseModel):
    symbol: str
    side: str  # 'buy' or 'sell'
    amount: Decimal
    price: Optional[Decimal] = None
    order_type: str = 'limit' # 'limit' or 'market'

class Position(BaseModel):
    symbol: str
    side: str # 'long' or 'short'
    size: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal

class ExchangeBase(ABC):
    """Abstract Base Class for all Exchange integrations."""
    
    def __init__(self, name: str, api_key: str = None, api_secret: str = None):
        self.name = name
        self.api_key = api_key
        self.api_secret = api_secret
        self.connected = False

    @abstractmethod
    async def connect(self):
        """Establish connection (auth/session)."""
        pass

    @abstractmethod
    async def get_balance(self, asset: str = "USDC") -> Decimal:
        """Get available balance for trading."""
        pass

    @abstractmethod
    async def get_price(self, symbol: str) -> Decimal:
        """Get current market price."""
        pass

    @abstractmethod
    async def open_position(self, symbol: str, side: str, amount: Decimal) -> Order:
        """Open a position (Long/Short)."""
        pass

    @abstractmethod
    async def close_position(self, symbol: str) -> Order:
        """Close an existing position completely."""
        pass

    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """Get all open positions."""
        pass
