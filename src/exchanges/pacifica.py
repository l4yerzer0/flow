import json
import time
import asyncio
import hmac
import hashlib
import ssl
import os
from decimal import Decimal
from typing import List, Optional, Dict
import aiohttp
import base58
import logging
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from solders.keypair import Keypair
from .base import ExchangeBase, Order, Position

logger = logging.getLogger(__name__)

class PacificaExchange(ExchangeBase):
    """Implementation for Pacifica DEX (Solana-based)."""
    
    BASE_URL = "https://api.pacifica.fi/api/v1"
    
    def __init__(self, name: str, api_key: str = "", api_secret: str = "", subaccount_id: str = "0", proxy: Optional[str] = None):
        super().__init__(name, api_key, api_secret, proxy)
        self.subaccount_id = subaccount_id
        self.keypair = None
        if api_secret:
            try:
                # Assuming api_secret is a Base58 encoded private key
                self.keypair = Keypair.from_base58_string(api_secret)
                # If api_key (public key) is not provided, derive it
                if not self.api_key:
                    self.api_key = str(self.keypair.pubkey())
            except Exception as e:
                print(f"Error initializing Pacifica keypair: {e}")

    async def connect(self):
        # Basic connectivity check or session initiation
        self.connected = True
        return True

    def _sign_message_bytes(self, message_bytes: bytes) -> str:
        if not self.keypair:
            raise ValueError("Private key (api_secret) is required for signing")
        signature_bytes = self.keypair.sign_message(message_bytes)
        return base58.b58encode(bytes(signature_bytes)).decode("utf-8")

    def _verify_signature(self, pubkey_b58: str, signature_b58: str, message_bytes: bytes) -> bool:
        try:
            pubkey_bytes = base58.b58decode(pubkey_b58)
            sig_bytes = base58.b58decode(signature_b58)
            Ed25519PublicKey.from_public_bytes(pubkey_bytes).verify(sig_bytes, message_bytes)
            return True
        except InvalidSignature:
            return False
        except Exception:
            return False

    def _get_signature(self, op_type: str, data: dict) -> dict:
        """
        Implements Pacifica's Ed25519 signing.
        Returns a dictionary with all required auth fields.
        """
        if not self.keypair:
            raise ValueError("Private key (api_secret) is required for signing")

        timestamp = int(time.time() * 1000)
        expiry_window = 300000 # Increased to match browser
        
        # Prepare "data to sign" object
        sign_obj = {
            "timestamp": timestamp,
            "expiry_window": expiry_window,
            "type": op_type,
            "data": data
        }
        
        # Canonicalization: Recursive sort keys
        def sort_dict(d):
            if isinstance(d, dict):
                return {k: sort_dict(v) for k, v in sorted(d.items())}
            if isinstance(d, list):
                return [sort_dict(i) for i in d]
            return d

        sorted_obj = sort_dict(sign_obj)
        
        # Compact JSON: No whitespace
        compact_json = json.dumps(sorted_obj, separators=(",", ":"))
        message_bytes = compact_json.encode("utf-8")
        
        # Sign using Ed25519
        signature_b58 = self._sign_message_bytes(message_bytes)
        
        pubkey_str = str(self.keypair.pubkey())
        res = {
            "account": self.api_key if self.api_key else pubkey_str,
            "signature": signature_b58,
            "timestamp": timestamp,
            "expiry_window": expiry_window,
        }
        
        if self.api_key and self.api_key != pubkey_str:
            res["agent_wallet"] = pubkey_str
            
        return res

    async def _request(self, method: str, endpoint: str, data: dict = None, sign_type: str = None, extra_headers: dict = None) -> dict:
        url = f"{self.BASE_URL}{endpoint}"
        
        payload = data or {}
        if sign_type:
            auth_fields = self._get_signature(sign_type, payload)
            # Flatten payload for the request
            payload.update(auth_fields)

        # Ensure no None values are sent in params or json
        payload = {k: v for k, v in payload.items() if v is not None}

        # Bypass SSL verification to fix "CERTIFICATE_VERIFY_FAILED" on some Windows systems
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        headers = {}
        if extra_headers:
            headers.update(extra_headers)

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                # For GET requests we use params, for others - json body
                kwargs = {"ssl": ssl_ctx, "headers": headers}
                if self.proxy:
                    kwargs["proxy"] = self.proxy
                    
                if method == "GET":
                    kwargs["params"] = payload
                else:
                    kwargs["json"] = payload

                async with session.request(method, url, **kwargs) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise Exception(f"Pacifica API Error: {resp.status} - {text}")
                    return await resp.json()
        except asyncio.TimeoutError:
            raise Exception("Pacifica API Error: Timeout")
        except aiohttp.ClientError as e:
            raise Exception(f"Pacifica API Error: Network error - {e}")

    async def get_balance(self, asset: str = "USDC") -> Decimal:
        # According to docs: GET /account
        resp = await self._request("GET", "/account", {"account": self.api_key}, sign_type="account")
        
        data = resp.get("data", {})
        if not data:
            return Decimal("0")
            
        # Try to find specific subaccount balance
        subaccounts = data.get("subaccounts", [])
        for sub in subaccounts:
            if str(sub.get("id")) == str(self.subaccount_id):
                return Decimal(str(sub.get("total_value", 0)))
        
        # Fallback to main account total value if subaccounts are empty or ID 0 is just the main one
        total_val = data.get("total_value") or data.get("balance") or 0
        return Decimal(str(total_val))

    async def get_price(self, symbol: str) -> Decimal:
        # Internally we use BTC-PERP, but API might use just BTC
        clean_symbol = symbol.replace("-PERP", "").upper()

        # Primary endpoint from Pacifica docs.
        try:
            resp = await self._request("GET", "/info/prices")
            prices = resp.get("data", [])
            if isinstance(prices, list):
                for item in prices:
                    item_sym = str(item.get("symbol", "")).upper()
                    if item_sym == clean_symbol or item_sym == f"{clean_symbol}-PERP":
                        # Use 'mark' or 'mid' price
                        return Decimal(str(item.get("mark") or item.get("price") or item.get("mid") or 0))
        except Exception as e:
            logger.warning(f"Pacifica /info/prices failed: {e}")

        # Return 0 instead of failing completely
        return Decimal("0")

    async def get_markets(self) -> List[str]:
        """
        Returns underlying symbols common format (BTC, ETH, SOL...).
        Pacifica uses instrument symbols like BTC-PERP in /info.
        """
        try:
            resp = await self._request("GET", "/info")
            instruments = resp.get("data", [])
            if not isinstance(instruments, list):
                return []

            assets = set()
            for item in instruments:
                symbol = str(item.get("symbol", "")).upper()
                if not symbol:
                    continue
                underlying = symbol.replace("-PERP", "")
                if underlying:
                    assets.add(underlying)
            return sorted(assets)
        except Exception as e:
            logger.error(f"Pacifica get_markets error: {e}")
            return []

    async def open_position(
        self, 
        symbol: str, 
        side: str, 
        amount: Decimal, 
        price: Optional[Decimal] = None, 
        order_type: str = 'market'
    ) -> Order:
        data = {
            "symbol": symbol,
            "side": "bid" if side == "buy" else "ask",
            "amount": float(amount),
            "subaccount_id": self.subaccount_id,
            "order_type": order_type
        }
        if order_type == 'limit' and price:
            data["price"] = float(price)
            
        resp = await self._request("POST", "/order/create", data, sign_type="create_order")
        return Order(
            symbol=symbol,
            side=side,
            amount=amount,
            price=Decimal(str(resp.get("price") or price or 0)),
            order_type=order_type
        )

    async def close_position(self, symbol: str) -> Order:
        # Simplification: usually means market order in opposite side
        # Need to know current size
        positions = await self.get_positions()
        pos = next((p for p in positions if p.symbol == symbol), None)
        if not pos:
            return None
        
        side = "sell" if pos.side == "long" else "buy"
        return await self.open_position(symbol, side, pos.size)

    async def get_positions(self) -> List[Position]:
        resp = await self._request("GET", "/positions", {"account": self.api_key}, sign_type="get_positions")
        positions = []
        for p in resp.get("positions", []):
            positions.append(Position(
                symbol=p["symbol"],
                side="long" if p["side"] == "bid" else "short",
                size=Decimal(str(p["amount"])),
                entry_price=Decimal(str(p["entry_price"])),
                unrealized_pnl=Decimal(str(p.get("unrealized_pnl", 0)))
            ))
        return positions

    async def get_funding_rate(self, symbol: str) -> Decimal:
        """Fetch funding rate from /info endpoint if available."""
        normalized_symbol = symbol.upper()
        if not normalized_symbol.endswith("-PERP"):
            normalized_symbol = f"{normalized_symbol}-PERP"
        try:
            resp = await self._request("GET", "/info")
            instruments = resp.get("data", [])
            if isinstance(instruments, list):
                for item in instruments:
                    if item.get("symbol") == normalized_symbol:
                        # Documentation says field is "funding", using "funding_rate" as fallback
                        rate = item.get("funding") or item.get("funding_rate") or 0
                        return Decimal(str(rate))
        except Exception as e:
            logger.warning(f"Pacifica get_funding_rate failed: {e}")
            
        return Decimal("0.0")

    async def get_points(self) -> Decimal:
        try:
            timestamp = int(time.time() * 1000)
            expiry_window = 300000

            account = self.api_key if self.api_key else str(self.keypair.pubkey())
            agent_wallet = str(self.keypair.pubkey())

            sign_obj = {
                "data": {},
                "expiry_window": expiry_window,
                "timestamp": timestamp,
                "type": "get_points",
            }
            
            compact_json = json.dumps(sign_obj, separators=(",", ":"))
            signature_b58 = self._sign_message_bytes(compact_json.encode("utf-8"))

            payload = {
                "account": account,
                "agent_wallet": agent_wallet,
                "signature": signature_b58,
                "timestamp": timestamp,
                "expiry_window": expiry_window,
            }

            headers = {
                "Origin": "https://app.pacifica.fi",
                "Referer": "https://app.pacifica.fi/"
            }

            resp = await self._request("POST", "/account/points", payload, sign_type=None, extra_headers=headers)
                
            data = resp.get("data") or {}
            pts = data.get("points", "0")
            if pts is None: pts = "0"
            return Decimal(str(pts))
        except Exception as e:
            logger.error(f"Pacifica get_points error: {e}", exc_info=True)
            return Decimal("0")

    async def get_volumes(self) -> Dict[str, Decimal]:
        try:
            resp = await self._request("GET", "/portfolio/volume", {"account": self.api_key})
            data = resp.get("data", {})
            return {
                "24h": Decimal(str(data.get("volume_1d", "0"))),
                "all_time": Decimal(str(data.get("volume_all_time", "0")))
            }
        except Exception as e:
            logger.error(f"Pacifica get_volumes error: {e}")
            return {"24h": Decimal("0"), "all_time": Decimal("0")}

    async def get_all_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """Fetch all prices and funding rates in two bulk requests."""
        results = {}
        try:
            # Parallel fetch prices and info
            prices_resp, info_resp = await asyncio.gather(
                self._request("GET", "/info/prices"),
                self._request("GET", "/info")
            )
            
            # Map prices
            prices_data = prices_resp.get("data", [])
            for p in prices_data:
                sym = str(p.get("symbol", "")).upper()
                if not sym.endswith("-PERP"): sym = f"{sym}-PERP"
                price = Decimal(str(p.get("mark") or p.get("price") or p.get("mid") or 0))
                results[sym] = {"price": price, "funding": Decimal("0")}

            # Map funding
            info_data = info_resp.get("data", [])
            for item in info_data:
                sym = str(item.get("symbol", "")).upper()
                if not sym.endswith("-PERP"): sym = f"{sym}-PERP"
                # If we don't have price for this sym yet, initialize it
                if sym not in results:
                    results[sym] = {"price": Decimal("0"), "funding": Decimal("0")}
                
                # Documentation says "funding", using "funding_rate" as fallback
                rate = item.get("funding") or item.get("funding_rate") or 0
                results[sym]["funding"] = Decimal(str(rate))
                
        except Exception as e:
            logger.error(f"Pacifica get_all_market_data error: {e}")
            
        return results
