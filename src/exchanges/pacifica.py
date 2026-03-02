import json
import time
import asyncio
import hmac
import hashlib
from decimal import Decimal
from typing import List, Optional, Dict
import aiohttp
import base58
from solders.keypair import Keypair
from .base import ExchangeBase, Order, Position

class PacificaExchange(ExchangeBase):
    """Implementation for Pacifica DEX (Solana-based)."""
    
    BASE_URL = "https://api.pacifica.fi/api/v1"
    
    def __init__(self, name: str, api_key: str = "", api_secret: str = "", subaccount_id: str = "0"):
        super().__init__(name, api_key, api_secret)
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

    def _get_signature(self, op_type: str, data: dict) -> dict:
        """
        Implements Pacifica's Ed25519 signing.
        Returns a dictionary with all required auth fields.
        """
        if not self.keypair:
            raise ValueError("Private key (api_secret) is required for signing")

        timestamp = int(time.time() * 1000)
        expiry_window = 30000
        
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
        signature_bytes = self.keypair.sign_message(message_bytes)
        signature_b58 = base58.b58encode(bytes(signature_bytes)).decode("utf-8")
        
        return {
            "account": str(self.keypair.pubkey()),
            "signature": signature_b58,
            "timestamp": timestamp,
            "expiry_window": expiry_window,
            "agent_wallet": None
        }

    async def _request(self, method: str, endpoint: str, data: dict = None, sign_type: str = None) -> dict:
        url = f"{self.BASE_URL}{endpoint}"
        
        payload = data or {}
        if sign_type:
            auth_fields = self._get_signature(sign_type, payload)
            # Flatten payload for the request
            payload.update(auth_fields)

        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, json=payload if method != "GET" else None, params=payload if method == "GET" else None) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Pacifica API Error: {resp.status} - {text}")
                return await resp.json()

    async def get_balance(self, asset: str = "USDC") -> Decimal:
        # GET /account/info requires signature (account/info type)
        resp = await self._request("GET", "/account/info", {"account": self.api_key}, sign_type="account_info")
        # Structure depends on real API, but usually contains subaccounts or direct balance
        # Mocking extraction for now:
        return Decimal(str(resp.get("balance", 0)))

    async def get_price(self, symbol: str) -> Decimal:
        # Public endpoint usually
        resp = await self._request("GET", f"/market/price", {"symbol": symbol})
        return Decimal(str(resp.get("price", 0)))

    async def open_position(self, symbol: str, side: str, amount: Decimal) -> Order:
        data = {
            "symbol": symbol,
            "side": "bid" if side == "buy" else "ask",
            "amount": float(amount),
            "subaccount_id": self.subaccount_id
        }
        resp = await self._request("POST", "/order/create", data, sign_type="create_order")
        return Order(
            symbol=symbol,
            side=side,
            amount=amount,
            price=Decimal(str(resp.get("price", 0))),
            order_type="market"
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
