import time
import json
import logging
import traceback
import asyncio
import cloudscraper
import os
from typing import List, Dict, Any, Optional, Union
from decimal import Decimal, ROUND_HALF_UP
from web3 import Web3
from eth_account.messages import encode_defunct
from ..core.base import BaseDEX

# Configure logging
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logging.basicConfig(level=logging.INFO)

TOKEN_CACHE_FILE = "variationals_tokens.json"

def _load_cache():
    if os.path.exists(TOKEN_CACHE_FILE):
        try:
            with open(TOKEN_CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load token cache: {e}")
    return {}

def _save_cache(cache):
    try:
        with open(TOKEN_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception as e:
        logger.error(f"Failed to save token cache: {e}")

# Global in-memory cache for tokens to avoid re-auth on every request
# Key: Wallet Address, Value: Access Token
_TOKEN_CACHE = _load_cache()
# Global locks to prevent concurrent auth requests for the same wallet
_AUTH_LOCKS = {}
# Global cache for instrument details (ticks, precision)
_INSTRUMENT_CACHE = {}

VARIATIONALS_PUBLIC_API_URL = "https://omni-client-api.prod.ap-northeast-1.variational.io"
VARIATIONALS_INTERNAL_API_URL = "https://omni.variational.io/api"

class VariationalsConnector(BaseDEX):
    """
    Connector for Variationals Exchange (Omni).
    Uses EVM Wallet authentication:
    1. POST /auth/generate_signing_data -> get message
    2. Sign message
    3. POST /auth/login -> get token
    Uses cloudscraper to bypass Cloudflare protection.
    Matches the success pattern of root tester.py.
    """
    def __init__(self, api_key: str, api_secret: str, base_url: str = None, proxy_url: str = None, custom_config: str = None, **kwargs):
        # Default to Internal API for actions (trading, auth)
        default_url = VARIATIONALS_INTERNAL_API_URL
        super().__init__(api_key=api_key, api_secret=api_secret, base_url=base_url or default_url, proxy_url=proxy_url, **kwargs)
        
        self.wallet_address = Web3.to_checksum_address(api_key)
        self.private_key = api_secret
        if self.private_key.startswith("0x"):
            self.private_key = self.private_key[2:]
            
        # Try to load token from cache
        self.access_token = _TOKEN_CACHE.get(self.wallet_address)
        
        # Initialize cloudscraper (Minimal, matching tester.py)
        self.scraper = cloudscraper.create_scraper()
        
        # Configure Proxy if needed
        if self.proxy_url:
            logger.info(f"Using proxy for Variationals: {self.proxy_url}")
            self.scraper.proxies = {
                'http': self.proxy_url,
                'https': self.proxy_url
            }

    def _format_decimal(self, value: Decimal) -> str:
        """
        Formats Decimal to string without scientific notation and removes trailing zeros.
        """
        s = format(value, 'f')
        if '.' in s:
            s = s.rstrip('0').rstrip('.')
        return s

    def _round_and_format(self, value: Union[float, Decimal, str], step_size: Union[float, Decimal, str]) -> str:
        """
        Rounds value to the nearest multiple of step_size using Decimal for precision and formats as string.
        """
        if not value:
            return "0"
            
        d_value = Decimal(str(value))
        d_step = Decimal(str(step_size))
        
        if d_step <= 0:
            return self._format_decimal(d_value)
            
        # Round to nearest step
        rounded = (d_value / d_step).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * d_step
        
        return self._format_decimal(rounded)

    @classmethod
    async def fetch_markets(cls) -> List[Dict[str, Any]]:
        """
        Fetches market information from the exchange.
        Endpoint: GET /metadata/stats (Public API)
        """
        url = f"{VARIATIONALS_PUBLIC_API_URL}/metadata/stats"
        import httpx
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    data = response.json()
                
                # The 'listings' key contains the market data
                if isinstance(data, dict) and "listings" in data:
                    return data["listings"]
                
                return []
            except Exception as e:
                logger.warning(f"Variationals fetch_markets attempt {attempt+1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    logger.error(f"Variationals fetch_markets failed after {max_retries} attempts: {e}")
                    raise e

    @classmethod
    async def fetch_prices(cls) -> List[Dict[str, Any]]:
        """
        Fetches current market prices from the exchange.
        Endpoint: GET /metadata/stats
        Since /metadata/stats contains both market info and prices (mark_price),
        we can reuse the logic but maybe filter/format if needed.
        """
        # For now, just return the listings which contain the prices
        return await cls.fetch_markets()

    async def get_positions(self) -> List[Dict[str, Any]]:
        """
        Fetches current open positions.
        Endpoint: GET /api/positions
        """
        try:
            data = await self._request("GET", "/positions", auth_required=True)
            # Variationals returns a list of objects containing position_info and price_info
            normalized = []
            for item in data:
                # Handle both direct and nested structures
                p = item.get('position_info', item)
                price_info = item.get('price_info', {})
                
                instr = p.get('instrument', {})
                underlying = instr.get('underlying', '')
                if not underlying:
                    continue
                    
                symbol = f"{underlying}-PERP"
                
                # Side: check explicit field or infer from qty
                qty_str = p.get('qty', '0')
                qty = float(qty_str)
                
                if qty == 0:
                    continue
                
                side = p.get('side', '').upper()
                if not side:
                    side = 'BUY' if qty > 0 else 'SELL'
                
                normalized_side = 'BUY' if side in ['BUY', 'LONG'] else 'SELL'
                
                # Entry price from position_info
                entry_price = float(p.get('avg_entry_price') or p.get('entry_price') or 0)
                
                # Current price from price_info
                current_price = float(price_info.get('price') or 0)
                
                normalized.append({
                    'symbol': symbol,
                    'side': normalized_side,
                    'amount': abs(qty),
                    'entry_price': entry_price,
                    'current_price': current_price,
                    'raw': item
                })
            return normalized
        except Exception as e:
            logger.error(f"Variationals get_positions error: {e}")
            return []

    async def _authenticate(self):
        """
        Performs the 2-step authentication flow using cloudscraper.
        Uses a lock to prevent concurrent authentication for the same wallet.
        """
        # Ensure a lock exists for this wallet
        if self.wallet_address not in _AUTH_LOCKS:
            _AUTH_LOCKS[self.wallet_address] = asyncio.Lock()
        
        lock = _AUTH_LOCKS[self.wallet_address]
        
        async with lock:
            # Double-check cache inside lock
            cached_token = _TOKEN_CACHE.get(self.wallet_address)
            if cached_token:
                logger.info("Using cached token (acquired inside lock)")
                self.access_token = cached_token
                return

            logger.info("Starting Variationals Authentication (Minimal cloudscraper)...")
            
            def step1_sync():
                payload = {"address": self.wallet_address}
                # Minimal headers, let cloudscraper handle it
                return self.scraper.post(f"{self.base_url}/auth/generate_signing_data", json=payload)

            logger.info(f"Auth Step 1: POST {self.base_url}/auth/generate_signing_data")
            resp1 = await asyncio.to_thread(step1_sync)
            
            if resp1.status_code >= 400:
                logger.error(f"Auth Step 1 Failed: {resp1.status_code} - {resp1.text}")
                resp1.raise_for_status()

            try:
                data = resp1.json()
                message_to_sign = data.get("signingData") or data.get("message")
            except:
                message_to_sign = resp1.text

            if not message_to_sign:
                logger.error(f"No message found. Response: {resp1.text}")
                return

            logger.info(f"Message to sign obtained (len={len(message_to_sign)})")

            # Step 2: Sign Message
            message = encode_defunct(text=message_to_sign)
            signed_message = Web3().eth.account.sign_message(message, private_key=self.private_key)
            signature = signed_message.signature.hex()
            if signature.startswith("0x"):
                signature = signature[2:]
            
            # Step 3: Login
            def step2_sync():
                payload = {
                    "address": self.wallet_address,
                    "signed_message": signature
                }
                return self.scraper.post(f"{self.base_url}/auth/login", json=payload)
                
            logger.info(f"Auth Step 2: POST {self.base_url}/auth/login")
            resp2 = await asyncio.to_thread(step2_sync)
            
            if resp2.status_code >= 400:
                logger.error(f"Auth Step 2 Failed: {resp2.status_code} - {resp2.text}")
                return
                
            data_step2 = resp2.json()
            self.access_token = data_step2.get("token") or data_step2.get("accessToken")
            
            if self.access_token:
                logger.info("Authentication Successful! Token received.")
                _TOKEN_CACHE[self.wallet_address] = self.access_token
                _save_cache(_TOKEN_CACHE)
            else:
                logger.warning(f"No token in response: {data_step2}")

    async def _request(self, method: str, endpoint: str, data: dict = None, auth_required: bool = False):
        url = f"{self.base_url}{endpoint}"
        
        if auth_required and not self.access_token:
            try:
                await self._authenticate()
            except Exception as e:
                logger.error(f"Auto-login failed: {e}")
                if auth_required:
                    raise

        if self.access_token:
            # Set auth cookies
            self.scraper.cookies.set("vr-token", self.access_token)
            self.scraper.cookies.set("vr-connected-address", self.wallet_address.lower())

        logger.info(f"Variationals Request (cloudscraper): {method} {url}")
        
        def request_sync():
            return self.scraper.request(method, url, json=data)

        try:
            response = await asyncio.to_thread(request_sync)
            logger.info(f"Variationals Response Status: {response.status_code}")
            
            if response.status_code == 401:
                    logger.warning("Token expired, clearing cache.")
                    self.access_token = None
                    if self.wallet_address in _TOKEN_CACHE:
                        del _TOKEN_CACHE[self.wallet_address]
                        _save_cache(_TOKEN_CACHE)
                    
            if response.status_code >= 400:
                logger.error(f"Variationals API Error Body: {response.text}")
                
            response.raise_for_status()
            
            try:
                return response.json()
            except json.JSONDecodeError:
                logger.error(f"Variationals JSON Decode Error. Raw response: {response.text}")
                raise
        except Exception as e:
            logger.error(f"Variationals Request Error: {e}")
            from ..core.base import CriticalTradingError, ConnectionTradingError
            
            # Check if it's an HTTP error from requests (cloudscraper uses requests)
            import requests
            if isinstance(e, requests.exceptions.HTTPError):
                status_code = e.response.status_code
                if status_code >= 500:
                    raise ConnectionTradingError(f"Variationals Server Error ({status_code})")
                else:
                    raise CriticalTradingError(f"Variationals API Error ({status_code}): {e.response.text}")
            elif isinstance(e, requests.exceptions.RequestException):
                raise ConnectionTradingError(f"Network error on Variationals: {str(e)}")
            
            raise e

    async def get_account_info(self) -> dict:
        """
        Fetches account portfolio/summary using the settlement_pools/details endpoint.
        """
        try:
            return await self._request("GET", "/settlement_pools/details", auth_required=True)
        except Exception as e:
            logger.error(f"Failed to fetch Variationals portfolio: {e}")
            return {}

    async def get_balance(self, token: str = "USDC") -> float:
        """
        Fetches balance from the details response.
        Actually returns margin balance (Equity).
        """
        try:
            data = await self.get_account_info()
            # Variationals returns margin_balance as the real equity
            equity = float(data.get("margin_balance") or data.get("balance", 0.0))
            logger.info(f"Variationals Equity: {equity}")
            return equity
        except Exception as e:
            logger.error(f"Variationals get_balance error: {e}")
            return 0.0

    async def get_price(self, pair: str) -> float:
        """
        Fetches current price for a pair using the Public Metadata API.
        """
        try:
            symbol = pair
            if not symbol.endswith("-PERP"):
                symbol = f"{symbol}-PERP"
                
            # Use fetch_markets which already handles cloudscraper and the public URL
            markets = await self.fetch_markets()
            
            # The public API returns a list of dictionaries (from debug_markets output)
            # Each dictionary has 'ticker' and 'mark_price'
            for m in markets:
                ticker = m.get("ticker", "")
                if ticker == symbol or f"{ticker}-PERP" == symbol:
                    return float(m.get("mark_price", 0.0))
                    
            return 0.0
        except Exception as e:
            logger.error(f"Variationals get_price error: {e}")
            return 0.0

    async def _create_indicative_quote(self, symbol: str, size: Union[float, str, Decimal]):
        """
        Request an indicative quote (RFQ) for a trade.
        Endpoint: POST /quotes/indicative
        """
        underlying = symbol.replace("-PERP", "")
        payload = {
            "instrument": {
                "underlying": underlying,
                "funding_interval_s": 3600,
                "settlement_asset": "USDC",
                "instrument_type": "perpetual_future"
            },
            "qty": str(size)
        }
        return await self._request("POST", "/quotes/indicative", data=payload, auth_required=True)

    async def _accept_quote(self, quote_id: str, side: str, is_reduce_only: bool = True, max_slippage: float = 0.01):
        """
        Accepts a quote to execute the trade (observed for Closing positions).
        Endpoint: POST /quotes/accept
        """
        payload = {
            "quote_id": quote_id,
            "side": side,
            "max_slippage": max_slippage,
            "is_reduce_only": is_reduce_only
        }
        return await self._request("POST", "/quotes/accept", data=payload, auth_required=True)

    async def _create_market_order(self, quote_id: str, side: str, is_reduce_only: bool = False, max_slippage: float = 0.005):
        """
        Executes a market order (observed for Opening positions).
        Endpoint: POST /orders/new/market
        """
        payload = {
            "quote_id": quote_id,
            "side": side,
            "max_slippage": max_slippage,
            "is_reduce_only": is_reduce_only
        }
        return await self._request("POST", "/orders/new/market", data=payload, auth_required=True)

    async def _create_limit_order(self, symbol: str, side: str, size: Union[float, str, Decimal], price: Union[float, str, Decimal], is_reduce_only: bool = False):
        """
        Creates a native limit order.
        Endpoint: POST /orders/new/limit
        """
        underlying = symbol.replace("-PERP", "")
        payload = {
            "order_type": "limit",
            "limit_price": str(price),
            "side": side.lower(),
            "instrument": {
                "underlying": underlying,
                "instrument_type": "perpetual_future",
                "settlement_asset": "USDC",
                "funding_interval_s": 3600
            },
            "qty": str(size),
            "is_auto_resize": False,
            "use_mark_price": False,
            "is_reduce_only": is_reduce_only
        }
        return await self._request("POST", "/orders/new/limit", data=payload, auth_required=True)

    async def get_market_info(self, symbol: str) -> Dict[str, Any]:
        """
        Gets market information for a specific symbol.
        """
        # Fallback to public fetch_markets
        markets = await self.fetch_markets()
        for m in markets:
            if m.get('ticker') == symbol or m.get('symbol') == symbol:
                return m
        return {}

    async def create_order(self, pair: str, side: str, amount: float, price: float = None, **kwargs) -> str:
        """
        Creates a new order.
        If 'price' is provided, uses native Limit Order endpoint.
        If 'price' is None, uses RFQ + Market Order flow.
        """
        try:
            is_reduce_only = kwargs.get('reduce_only', False)
            
            # Get market info for rounding
            market_info = await self.get_market_info(pair)
            logger.debug(f"Variationals Market Info for {pair}: {market_info}")
            
            # Use specific tick fields from /instruments or fallback
            # Variationals often uses 'qty_tick' or 'min_qty_tick'
            qty_step = market_info.get('qty_tick') or market_info.get('min_qty_tick') or market_info.get('min_qty') or '1'
            price_step = market_info.get('price_tick') or market_info.get('min_price_tick') or market_info.get('min_price') or '0.01'
            
            formatted_amount = self._round_and_format(amount, qty_step)
            # Ensure amount is at least one tick
            if float(formatted_amount) <= 0:
                formatted_amount = self._round_and_format(qty_step, qty_step)
            
            # 1. Native Limit Order
            if price is not None:
                formatted_price = self._round_and_format(price, price_step)
                logger.info(f"Placing Native Limit Order: {pair} {side} {formatted_amount} @ {formatted_price}")
                resp = await self._create_limit_order(pair, side, formatted_amount, formatted_price, is_reduce_only)
                return resp.get('rfq_id') or resp.get('order_id')

            # 2. Market Order / RFQ Flow (Legacy/Fallback)
            # Request Quote
            quote_data = await self._create_indicative_quote(pair, formatted_amount)
            logger.debug(f"Variationals Quote Data: {quote_data}")
            quote_id = quote_data.get('quote_id')
            
            if not quote_id:
                raise ValueError(f"Failed to get quote_id from indicative quote response: {quote_data}")

            # Execute Market Order
            # Use a default slippage
            max_slippage = kwargs.get('max_slippage', 0.005)
            normalized_side = side.lower()
            
            if is_reduce_only:
                resp = await self._accept_quote(quote_id, normalized_side, is_reduce_only=True, max_slippage=max_slippage)
            else:
                resp = await self._create_market_order(quote_id, normalized_side, is_reduce_only=False, max_slippage=max_slippage)
            
            return resp.get('rfq_id') or resp.get('order_id')
            
        except Exception as e:
            logger.error(f"Variationals create_order error: {e}")
            raise e
            
    async def close_position(self, symbol: str, amount: float, side: str):
        """
        Closes a position by creating a reduce-only order.
        """
        return await self.create_order(symbol, side, amount, params={"reduce_only": True})

    async def cancel_order(self, order_id: str, pair: str = None) -> bool:
        """
        Cancels an open order.
        Endpoint: POST /orders/cancel
        """
        try:
            payload = {"rfq_id": order_id}
            await self._request("POST", "/orders/cancel", data=payload, auth_required=True)
            logger.info(f"Successfully cancelled order {order_id}")
            return True
        except Exception as e:
            logger.error(f"Variationals cancel_order error: {e}")
            return False

    async def get_stats(self) -> List[Dict[str, Any]]:
        """
        Returns custom stats for Variationals.
        """
        try:
            data = await self.get_account_info()
            # Variationals API returns 'balance' (Equity) and we can calculate margin
            equity = float(data.get("balance", 0.0))
            
            # Simulate "Points" based on equity/volume (since real points API might not be public yet)
            # In real scenario, fetch from /points endpoint
            points = int(equity * 0.15) 
            
            return [
                {
                    "key": "var_equity",
                    "label": "Omni Equity",
                    "value": f"${equity:,.2f}",
                    "type": "currency",
                    "color": "blue"
                },
                {
                    "key": "var_points",
                    "label": "Omni Points",
                    "value": f"{points:,}",
                    "type": "number",
                    "color": "purple"
                }
            ]
        except Exception as e:
            logger.error(f"Error fetching stats: {e}")
            return []