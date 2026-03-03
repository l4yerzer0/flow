import json
import time
import asyncio
import logging
import os
from decimal import Decimal
from typing import List, Optional, Dict, Any, Union
import cloudscraper
from eth_account import Account
from eth_account.messages import encode_defunct
from .base import ExchangeBase, Order, Position

logger = logging.getLogger(__name__)

TOKEN_CACHE_FILE = "config/variational_tokens.json"

def _load_token_cache():
    if os.path.exists(TOKEN_CACHE_FILE):
        try:
            with open(TOKEN_CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load Variational token cache: {e}")
    return {}

def _save_token_cache(cache):
    try:
        os.makedirs(os.path.dirname(TOKEN_CACHE_FILE), exist_ok=True)
        with open(TOKEN_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception as e:
        logger.error(f"Failed to save Variational token cache: {e}")

class VariationalExchange(ExchangeBase):
    """
    Implementation for Variational DEX (Omni).
    Uses EVM Wallet authentication with internal API and Cloudscraper for Cloudflare bypass.
    Persists tokens to avoid frequent logins.
    """
    
    PUBLIC_API_URL = "https://omni-client-api.prod.ap-northeast-1.variational.io"
    INTERNAL_API_URL = "https://omni.variational.io/api"
    
    def __init__(self, name: str, api_key: str = "", api_secret: str = ""):
        """
        api_key: Wallet Address (0x...)
        api_secret: Private Key (0x...)
        """
        super().__init__(name, api_key, api_secret)
        self.wallet_address = api_key.lower()
        self.private_key = api_secret
        if self.private_key and self.private_key.startswith("0x"):
            self.private_key = self.private_key[2:]
            
        self.access_token = None
        self.scraper = cloudscraper.create_scraper()
        self.base_url = self.INTERNAL_API_URL
        
        # Load from persistent cache
        cache = _load_token_cache()
        self.access_token = cache.get(self.wallet_address)
        if self.access_token:
            self._apply_token(self.access_token)

    def _apply_token(self, token: str):
        """Apply token to scraper cookies."""
        self.scraper.cookies.set("vr-token", token)
        self.scraper.cookies.set("vr-connected-address", self.wallet_address)
        self.access_token = token

    async def connect(self):
        """Perform the 2-step authentication flow if token is missing."""
        if self.access_token:
            self.connected = True
            return True
            
        try:
            logger.info(f"Authenticating Variational for {self.wallet_address}...")
            # Step 1: Generate signing data
            payload = {"address": self.wallet_address}
            resp1 = await asyncio.to_thread(
                lambda: self.scraper.post(f"{self.base_url}/auth/generate_signing_data", json=payload)
            )
            resp1.raise_for_status()
            data1 = resp1.json()
            message_to_sign = data1.get("signingData") or data1.get("message")
            
            if not message_to_sign:
                raise ValueError("No message to sign found in response")
                
            # Step 2: Sign message
            message = encode_defunct(text=message_to_sign)
            signed_message = Account.sign_message(message, private_key=self.private_key)
            signature = signed_message.signature.hex()
            if signature.startswith("0x"):
                signature = signature[2:]
                
            # Step 3: Login
            login_payload = {
                "address": self.wallet_address,
                "signed_message": signature
            }
            resp2 = await asyncio.to_thread(
                lambda: self.scraper.post(f"{self.base_url}/auth/login", json=login_payload)
            )
            resp2.raise_for_status()
            data2 = resp2.json()
            token = data2.get("token") or data2.get("accessToken")
            
            if token:
                self._apply_token(token)
                # Update persistent cache
                cache = _load_token_cache()
                cache[self.wallet_address] = token
                _save_token_cache(cache)
                
                self.connected = True
                logger.info("Variational authentication successful.")
                return True
            else:
                raise ValueError("No access token received")
                
        except Exception as e:
            logger.error(f"Variational Auth Error: {e}")
            self.connected = False
            return False

    async def _request(self, method: str, endpoint: str, data: dict = None, is_public: bool = False) -> dict:
        url = f"{self.PUBLIC_API_URL if is_public else self.INTERNAL_API_URL}{endpoint}"
        
        # Ensure we have a token for private requests
        if not is_public and not self.access_token:
            success = await self.connect()
            if not success:
                raise Exception("Authentication failed")

        def do_req():
            return self.scraper.request(method, url, json=data)

        resp = await asyncio.to_thread(do_req)
        
        # Handle expiration (401)
        if resp.status_code == 401 and not is_public:
            logger.warning("Variational token expired, re-authenticating...")
            self.access_token = None
            success = await self.connect()
            if success:
                resp = await asyncio.to_thread(do_req)
            
        resp.raise_for_status()
        return resp.json()

    async def get_balance(self, asset: str = "USDC") -> Decimal:
        try:
            # Endpoint from old connector: /settlement_pools/details
            data = await self._request("GET", "/settlement_pools/details")
            # balance or margin_balance
            equity = data.get("margin_balance") or data.get("balance", 0.0)
            return Decimal(str(equity))
        except Exception as e:
            logger.error(f"Variational get_balance error: {e}")
            return Decimal("0.0")

    async def get_price(self, symbol: str) -> Decimal:
        try:
            # Endpoint from old connector: /metadata/stats (Public)
            data = await self._request("GET", "/metadata/stats", is_public=True)
            ticker = symbol
            if not ticker.endswith("-PERP"):
                ticker = f"{ticker}-PERP"
                
            for m in data.get("listings", []):
                if m.get("ticker") == ticker:
                    return Decimal(str(m.get("mark_price", 0.0)))
            return Decimal("0.0")
        except Exception as e:
            logger.error(f"Variational get_price error: {e}")
            return Decimal("0.0")

    async def open_position(self, symbol: str, side: str, amount: Decimal) -> Order:
        try:
            # Variational RFQ Flow: 
            # 1. Indicative Quote -> 2. Market Order
            underlying = symbol.replace("-PERP", "")
            
            # Request Quote
            quote_payload = {
                "instrument": {
                    "underlying": underlying,
                    "funding_interval_s": 3600,
                    "settlement_asset": "USDC",
                    "instrument_type": "perpetual_future"
                },
                "qty": str(amount)
            }
            quote_data = await self._request("POST", "/quotes/indicative", data=quote_payload)
            quote_id = quote_data.get("quote_id")
            
            if not quote_id:
                raise ValueError("Failed to get quote_id")
                
            # Create Order
            order_payload = {
                "quote_id": quote_id,
                "side": side.lower(),
                "max_slippage": 0.01,
                "is_reduce_only": False
            }
            # The old connector used /orders/new/market
            resp = await self._request("POST", "/orders/new/market", data=order_payload)
            
            return Order(
                symbol=symbol,
                side=side,
                amount=amount,
                price=None, # Market order
                order_type="market"
            )
        except Exception as e:
            logger.error(f"Variational open_position error: {e}")
            raise e

    async def close_position(self, symbol: str) -> Order:
        try:
            positions = await self.get_positions()
            pos = next((p for p in positions if p.symbol == symbol), None)
            if not pos:
                return None
            
            # RFQ for close (reduce only)
            underlying = symbol.replace("-PERP", "")
            quote_payload = {
                "instrument": {
                    "underlying": underlying,
                    "funding_interval_s": 3600,
                    "settlement_asset": "USDC",
                    "instrument_type": "perpetual_future"
                },
                "qty": str(pos.size)
            }
            quote_data = await self._request("POST", "/quotes/indicative", data=quote_payload)
            quote_id = quote_data.get("quote_id")
            
            # Accept quote (reduce only)
            close_side = "sell" if pos.side == "long" else "buy"
            accept_payload = {
                "quote_id": quote_id,
                "side": close_side,
                "max_slippage": 0.01,
                "is_reduce_only": True
            }
            # Old connector used /quotes/accept for closing
            await self._request("POST", "/quotes/accept", data=accept_payload)
            
            return Order(
                symbol=symbol,
                side=close_side,
                amount=pos.size,
                price=None,
                order_type="market"
            )
        except Exception as e:
            logger.error(f"Variational close_position error: {e}")
            raise e

    async def get_positions(self) -> List[Position]:
        try:
            data = await self._request("GET", "/positions")
            positions = []
            for item in data:
                p = item.get('position_info', item)
                instr = p.get('instrument', {})
                underlying = instr.get('underlying', '')
                if not underlying: continue
                
                qty = Decimal(p.get('qty', '0'))
                if qty == 0: continue
                
                # In Variational, side is often BUY/SELL
                raw_side = p.get('side', '').upper()
                side = 'long' if raw_side in ['BUY', 'LONG'] else 'short'
                if not raw_side:
                    side = 'long' if qty > 0 else 'short'
                
                positions.append(Position(
                    symbol=f"{underlying}-PERP",
                    side=side,
                    size=abs(qty),
                    entry_price=Decimal(p.get('avg_entry_price') or p.get('entry_price') or 0),
                    unrealized_pnl=Decimal(item.get('price_info', {}).get('unrealized_pnl') or 0)
                ))
            return positions
        except Exception as e:
            logger.error(f"Variational get_positions error: {e}")
            return []
