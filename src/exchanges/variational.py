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
    
    def __init__(self, name: str, api_key: str = "", api_secret: str = "", proxy: Optional[str] = None):
        """
        api_key: Wallet Address (0x...)
        api_secret: Private Key (0x...)
        """
        super().__init__(name, api_key, api_secret, proxy)
        self.wallet_address = (api_key or "").lower()
        self.private_key = api_secret
        if self.private_key and self.private_key.startswith("0x"):
            self.private_key = self.private_key[2:]
            
        self.access_token = None
        self.scraper = cloudscraper.create_scraper()
        if self.proxy:
            self.scraper.proxies = {
                "http": self.proxy,
                "https": self.proxy,
            }
        self.base_url = self.INTERNAL_API_URL
        
        # Load from persistent cache
        if self.wallet_address:
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
            
        if not self.wallet_address or not self.private_key:
            raise ValueError("Wallet address and private key are required for Variational")

        try:
            # Step 1: Generate signing data
            payload = {"address": self.wallet_address}
            resp1 = await asyncio.to_thread(
                lambda: self.scraper.post(f"{self.base_url}/auth/generate_signing_data", json=payload)
            )
            if resp1.status_code != 200:
                raise Exception(f"Generate signing data failed: {resp1.status_code} - {resp1.text}")
                
            # Handle both JSON and raw text responses
            try:
                data1 = resp1.json()
                message_to_sign = data1.get("signingData") or data1.get("message")
            except Exception:
                # Fallback to raw text if not JSON
                message_to_sign = resp1.text

            if not message_to_sign:
                raise ValueError(f"No message to sign found. Response: {resp1.text[:100]}")
                
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
            if resp2.status_code != 200:
                raise Exception(f"Login failed: {resp2.status_code} - {resp2.text}")
                
            data2 = resp2.json()
            token = data2.get("token") or data2.get("accessToken")
            
            if token:
                self._apply_token(token)
                # Update persistent cache
                cache = _load_token_cache()
                cache[self.wallet_address] = token
                _save_token_cache(cache)
                
                self.connected = True
                return True
            else:
                raise ValueError(f"No access token in response: {data2}")
                
        except Exception as e:
            self.connected = False
            raise e

    async def _request(self, method: str, endpoint: str, data: dict = None, is_public: bool = False) -> dict:
        url = f"{self.PUBLIC_API_URL if is_public else self.INTERNAL_API_URL}{endpoint}"
        
        # Ensure we have a token for private requests
        if not is_public and not self.access_token:
            success = await self.connect()
            if not success:
                raise Exception("Authentication failed")

        def do_req():
            # Add a 10 second timeout to prevent hanging forever on Cloudflare challenges or network issues
            return self.scraper.request(method, url, json=data, timeout=10)

        try:
            resp = await asyncio.to_thread(do_req)
        except Exception as e:
            raise Exception(f"Variational Network/Cloudflare Error: {e}")
        
        # Handle expiration (401)
        if resp.status_code == 401 and not is_public:
            logger.warning("Variational token expired, re-authenticating...")
            self.access_token = None
            success = await self.connect()
            if success:
                try:
                    resp = await asyncio.to_thread(do_req)
                except Exception as e:
                    raise Exception(f"Variational Network/Cloudflare Error after re-auth: {e}")
            
        if resp.status_code != 200:
            raise Exception(f"Variational API Error: {resp.status_code} - {resp.text}")
        return resp.json()

    async def get_balance(self, asset: str = "USDC") -> Decimal:
        # Endpoint from old connector: /settlement_pools/details
        data = await self._request("GET", "/settlement_pools/details")
        # balance or margin_balance
        equity = data.get("margin_balance") or data.get("balance", 0.0)
        return Decimal(str(equity))

    async def get_price(self, symbol: str) -> Decimal:
        clean_symbol = symbol.replace("-PERP", "").upper()
        try:
            # Endpoint from old connector: /metadata/stats (Public)
            data = await self._request("GET", "/metadata/stats", is_public=True)
            for m in data.get("listings", []):
                ticker = str(m.get("ticker", "")).upper()
                if ticker == clean_symbol or ticker == f"{clean_symbol}-PERP":
                    return Decimal(str(m.get("mark_price", 0.0)))
            return Decimal("0.0")
        except Exception as e:
            logger.warning(f"Variational get_price failed for {symbol}: {e}")
            return Decimal("0.0")

    async def get_markets(self) -> List[str]:
        """
        Returns list of underlyings in common format (BTC, ETH, SOL...).
        Source: public /metadata/stats endpoint.
        """
        try:
            data = await self._request("GET", "/metadata/stats", is_public=True)
            assets = set()
            for m in data.get("listings", []):
                ticker = str(m.get("ticker", "")).upper()
                if not ticker:
                    continue
                underlying = ticker.replace("-PERP", "")
                if underlying:
                    assets.add(underlying)
            return sorted(assets)
        except Exception as e:
            logger.error(f"Variational get_markets error: {e}")
            return []

    async def open_position(
        self, 
        symbol: str, 
        side: str, 
        amount: Decimal, 
        price: Optional[Decimal] = None, 
        order_type: str = 'market'
    ) -> Order:
        try:
            underlying = symbol.replace("-PERP", "")
            
            if order_type == 'limit' and price is not None:
                limit_payload = {
                    "order_type": "limit",
                    "limit_price": str(price),
                    "side": side.lower(),
                    "instrument": {
                        "underlying": underlying,
                        "instrument_type": "perpetual_future",
                        "settlement_asset": "USDC",
                        "funding_interval_s": 3600
                    },
                    "qty": str(amount),
                    "slippage_limit": "0.005",
                    "is_auto_resize": False,
                    "use_mark_price": False,
                    "is_reduce_only": False
                }
                resp = await self._request("POST", "/orders/new/limit", data=limit_payload)
                return Order(
                    symbol=symbol,
                    side=side,
                    amount=amount,
                    price=price,
                    order_type="limit"
                )
            
            # Variational Market RFQ Flow: 
            # 1. Indicative Quote -> 2. Market Order
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
            resp = await self._request("POST", "/orders/new/market", data=order_payload)
            
            return Order(
                symbol=symbol,
                side=side,
                amount=amount,
                price=price or Decimal(str(quote_data.get("price", 0))),
                order_type=order_type
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

    async def get_funding_rate(self, symbol: str) -> Decimal:
        """Fetch funding rate from public metadata/stats endpoint."""
        clean_symbol = symbol.replace("-PERP", "").upper()
        try:
            data = await self._request("GET", "/metadata/stats", is_public=True)
            for m in data.get("listings", []):
                ticker = str(m.get("ticker", "")).upper()
                if ticker == clean_symbol or ticker == f"{clean_symbol}-PERP":
                    # Often provided as 'funding_rate' or 'current_funding_rate'
                    return Decimal(str(m.get("funding_rate") or m.get("current_funding_rate") or 0.0))
            return Decimal("0.0")
        except Exception as e:
            logger.error(f"Variational get_funding_rate error: {e}")
            return Decimal("0.0")

    async def get_points(self) -> Decimal:
        try:
            data = await self._request("GET", "/points/summary")
            # Expected response: {"total_points": "0.879061", ...}
            pts = data.get("total_points") or data.get("self_points") or "0"
            return Decimal(str(pts))
        except Exception as e:
            logger.error(f"Variational get_points error: {e}")
            return Decimal("0")

    async def get_volumes(self) -> Dict[str, Decimal]:
        try:
            data = await self._request("GET", "/portfolio/trade_volume")
            # Using total.lifetime for 'all_time' and last_30d as proxy for '24h' if 24h is not available
            # or just map what we have
            total = data.get("total") or {}
            return {
                "24h": Decimal(str(data.get("last_30d", "0"))), # Note: using 30d since 24h is missing in response
                "all_time": Decimal(str(total.get("lifetime", "0")))
            }
        except Exception as e:
            logger.error(f"Variational get_volumes error: {e}")
            return {"24h": Decimal("0"), "all_time": Decimal("0")}
