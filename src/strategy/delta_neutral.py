import time
import asyncio
import logging
from decimal import Decimal
from typing import Optional, Dict
from enum import Enum
from ..exchanges.base import ExchangeBase

class StrategyState(Enum):
    IDLE = "IDLE"
    WAITING_MAKER = "WAITING_MAKER"
    HEDGED = "HEDGED"
    CLOSING = "CLOSING"

class TradeContext:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.state = StrategyState.IDLE
        self.current_pnl = Decimal("0.0")
        self.ex_a_side: Optional[str] = None
        self.ex_b_side: Optional[str] = None
        self.maker_ex: Optional[ExchangeBase] = None
        self.taker_ex: Optional[ExchangeBase] = None
        self.maker_side: Optional[str] = None
        self.taker_side: Optional[str] = None
        self.target_size_usd = Decimal("0.0")
        self.target_tp_usd = Decimal("0.0")
        self.maker_order_time: float = 0.0

class DeltaNeutralStrategy:
    def __init__(self, exchange_a: ExchangeBase, exchange_b: ExchangeBase):
        self.ex_a = exchange_a
        self.ex_b = exchange_b
        self.target_size_usd = Decimal("1000.00")
        self.logger = logging.getLogger("strategy")
        self.log_callback = None
        
        # We will dynamically set this from BotManager
        self.available_symbols: list[str] = []
        
        # Active trades mapped by symbol
        self.trades: Dict[str, TradeContext] = {}
        
        # Blacklist for symbols that failed to place orders (unsupported instruments, etc.)
        self.blacklisted_symbols: set[str] = set()
        
        # Strategy Parameters
        self.min_spread_bps = Decimal("15.0")
        self.take_profit_usd = Decimal("2.0")
        self.fee_rate_bps = Decimal("4.0")
        self.max_concurrent_trades = 1
        
        # Session Management
        self.target_session_volume = Decimal("0.0")
        self.balance_percent = Decimal("0.0")
        self.min_position_size = Decimal("11.0")
        self.current_session_volume = Decimal("0.0")
        
        # Callbacks
        self.get_balance_a = lambda: Decimal("0.0")
        self.get_balance_b = lambda: Decimal("0.0")
        self.stop_bot_callback = None

    @property
    def current_pnl(self) -> Decimal:
        return sum(t.current_pnl for t in self.trades.values())

    @property
    def state(self) -> StrategyState:
        # For UI display purposes: if any trade is hedged, return HEDGED. 
        # Otherwise, if any trade is opening/closing, return that. Else IDLE.
        states = [t.state for t in self.trades.values()]
        if StrategyState.HEDGED in states: return StrategyState.HEDGED
        if StrategyState.CLOSING in states: return StrategyState.CLOSING
        if StrategyState.WAITING_MAKER in states: return StrategyState.WAITING_MAKER
        return StrategyState.IDLE

    def _log(self, msg: str, color: str = "white"):
        if self.log_callback:
            self.log_callback(msg, color)

    def _calculate_trade_size(self, edge_bps: Decimal = Decimal("0.0")) -> Decimal:
        if self.balance_percent > 0:
            bal_a = self.get_balance_a()
            bal_b = self.get_balance_b()
            min_bal = min(bal_a, bal_b)
            
            total_allowed = min_bal * (self.balance_percent / Decimal("100.0"))
            used_capital = sum(getattr(t, 'target_size_usd', Decimal("0.0")) for t in self.trades.values())
            
            available_capital = max(Decimal("0.0"), total_allowed - used_capital)
            remaining_slots = self.max_concurrent_trades - len(self.trades)
            
            if remaining_slots <= 0 or available_capital <= 0:
                return Decimal("0.0")
                
            # Base distribution: equal slices of available capital
            base_size = available_capital / Decimal(remaining_slots)
            
            # Bot decision based on edge: if edge is very high, take more of the available capital
            threshold = self.min_spread_bps + self.fee_rate_bps
            if threshold > 0:
                edge_multiplier = min(Decimal("1.5"), max(Decimal("1.0"), edge_bps / threshold))
            else:
                edge_multiplier = Decimal("1.0")
                
            dynamic_size = base_size * edge_multiplier
            
            # Ensure we don't exceed available capital
            final_size = min(dynamic_size, available_capital)
            
            if final_size >= self.min_position_size:
                return final_size
            return Decimal("0.0")
            
        return max(self.target_size_usd, self.min_position_size)

    async def run_loop(self):
        self._log(f"Strategy loop started. Max concurrent trades: {self.max_concurrent_trades}", "info")
        while True:
            try:
                # Check session volume limit
                if self.target_session_volume > 0 and self.current_session_volume >= self.target_session_volume:
                    self._log(f"Target session volume reached (${self.current_session_volume:,.2f} >= ${self.target_session_volume:,.2f}). Stopping bot.", "yellow")
                    if self.stop_bot_callback:
                        self.stop_bot_callback()
                    break

                # 1. Process existing active trades
                for sym, ctx in list(self.trades.items()):
                    if ctx.state == StrategyState.WAITING_MAKER:
                        await self._handle_waiting_maker(ctx)
                    elif ctx.state == StrategyState.HEDGED:
                        await self._handle_hedged(ctx)
                    elif ctx.state == StrategyState.CLOSING:
                        await self._handle_closing(ctx)

                # 2. Look for new opportunities if we have capacity
                if len(self.trades) < self.max_concurrent_trades:
                    if self.available_symbols:
                        await self._scan_for_opportunities()
                    else:
                        if getattr(self, '_empty_sym_counter', 0) % 10 == 0:
                            self._log("No available symbols to scan. Waiting for market data...", "dim")
                        self._empty_sym_counter = getattr(self, '_empty_sym_counter', 0) + 1

            except Exception as e:
                self._log(f"Strategy Error: {e}", "red")
                self.logger.error(f"Strategy Error: {e}")
                await asyncio.sleep(5)
            
            await asyncio.sleep(1)

    async def _scan_for_opportunities(self):
        """Main scanning loop with bulk data fetching."""
        best_sym = None
        best_edge = Decimal("-999999.0")
        best_data = None

        # We only check symbols not currently trading and not blacklisted
        symbols_to_check = [s for s in self.available_symbols if s not in self.trades and s not in self.blacklisted_symbols]
        
        if not symbols_to_check:
            return

        # Added heartbeat
        if getattr(self, '_scan_counter', 0) % 10 == 0:
            self._log(f"Scanning {len(symbols_to_check)} symbols (Bulk Mode)...", "dim")
        self._scan_counter = getattr(self, '_scan_counter', 0) + 1

        try:
            # Bulk fetch from both exchanges
            data_results = await asyncio.gather(
                self.ex_a.get_all_market_data(),
                self.ex_b.get_all_market_data(),
                return_exceptions=True
            )
            
            if any(isinstance(r, Exception) for r in data_results):
                self._log(f"Bulk fetch error: {data_results}", "dim")
                return

            data_a, data_b = data_results

            for sym in symbols_to_check:
                # Ensure symbol has -PERP suffix for lookup
                lookup_sym = sym if sym.endswith("-PERP") else f"{sym}-PERP"
                
                m_a = data_a.get(lookup_sym)
                m_b = data_b.get(lookup_sym)
                
                if not m_a or not m_b:
                    continue
                
                price_a = m_a["price"]
                price_b = m_b["price"]
                fund_a = m_a["funding"]
                fund_b = m_b["funding"]

                if price_a == 0 or price_b == 0: 
                    continue

                spread_pct = abs(price_a - price_b) / min(price_a, price_b)
                spread_bps = spread_pct * 10000

                if price_a > price_b:
                    net_funding = fund_a - fund_b
                else:
                    net_funding = fund_b - fund_a
                
                net_funding_bps = net_funding * 10000
                effective_edge_bps = spread_bps + net_funding_bps
                
                # Debug logging for major pairs
                if getattr(self, '_scan_counter', 0) % 20 == 1 and sym in ["BTC-PERP", "ETH-PERP", "SUI-PERP"]:
                    self._log(f"[{sym}] Debug - P_A:{price_a}, P_B:{price_b}, F_A:{fund_a:.6f}, F_B:{fund_b:.6f} -> Edge:{effective_edge_bps:.2f}", "dim")

                if effective_edge_bps > best_edge:
                    best_edge = effective_edge_bps
                    best_sym = sym
                    best_data = (price_a, price_b)
                    
        except Exception as e:
            self._log(f"Scan loop exception: {e}", "red")
            return

        # If best edge meets criteria, execute
        if best_sym and best_edge >= (self.min_spread_bps + self.fee_rate_bps):
            price_a, price_b = best_data
            trade_size_usd = self._calculate_trade_size(best_edge)

            # Check if adding this trade exceeds session volume (each leg counts, so * 2)
            if self.target_session_volume > 0:
                expected_new_vol = self.current_session_volume + (trade_size_usd * 2)
                if expected_new_vol > self.target_session_volume and self.current_session_volume > 0:
                    self._log(f"Skipping trade to not exceed session volume. Current: ${self.current_session_volume:,.2f}", "dim")
                    return

            self._log(
                f"[{best_sym}] Opportunity! Edge: {best_edge:.2f} bps. Size: ${trade_size_usd:.2f}",
                "green"
            )
            await self._open_new_trade(best_sym, price_a, price_b, trade_size_usd)
        elif best_sym:
            req_edge = self.min_spread_bps + self.fee_rate_bps
            # Log the top symbol every 10 loops so the user knows the bot is alive and seeing data
            if getattr(self, '_scan_counter', 0) % 10 == 1:
                self._log(
                    f"Highest edge right now: [{best_sym}] {best_edge:.2f} bps. Need: {req_edge:.2f} bps.",
                    "dim"
                )

    async def _open_new_trade(self, symbol: str, price_a: Decimal, price_b: Decimal, trade_size_usd: Decimal):
        ctx = TradeContext(symbol)
        
        if price_a > price_b:
            ctx.ex_a_side = 'sell'
            ctx.ex_b_side = 'buy'
            ctx.maker_ex = self.ex_a
            ctx.taker_ex = self.ex_b
            maker_price = price_a * Decimal("0.9999")
            # Round maker price to tick size (0.0001) to match exchange requirements
            maker_price = Decimal(str(round(float(maker_price), 4)))
            ctx.maker_side = ctx.ex_a_side
            ctx.taker_side = ctx.ex_b_side
        else:
            ctx.ex_a_side = 'buy'
            ctx.ex_b_side = 'sell'
            ctx.maker_ex = self.ex_b
            ctx.taker_ex = self.ex_a
            maker_price = price_b * Decimal("1.0001")
            # Round maker price to tick size (0.0001) to match exchange requirements
            maker_price = Decimal(str(round(float(maker_price), 4)))
            ctx.maker_side = ctx.ex_b_side
            ctx.taker_side = ctx.ex_a_side

        amount = trade_size_usd / maker_price
        take_profit = trade_size_usd * Decimal("0.002") # Dynamic TP based on actual size
        
        self._log(f"[{symbol}] Placing Maker {ctx.maker_side} on {ctx.maker_ex.name} at {maker_price:.4f}", "cyan")
        
        try:
            await ctx.maker_ex.open_position(symbol, ctx.maker_side, amount, price=maker_price, order_type='limit')
            ctx.state = StrategyState.WAITING_MAKER
            ctx.target_size_usd = trade_size_usd
            ctx.target_tp_usd = take_profit
            ctx.maker_order_time = time.time()
            self.trades[symbol] = ctx
            
            # Increment session volume (Maker side)
            self.current_session_volume += trade_size_usd
        except Exception as e:
            error_msg = str(e)
            self._log(f"[{symbol}] Failed to open Maker: {e}", "red")
            
            # Blacklist symbol on any failure to prevent infinite loop hanging
            self.blacklisted_symbols.add(symbol)
            if "unsupported instrument" in error_msg.lower() or "not found" in error_msg.lower():
                self._log(f"[{symbol}] Added to blacklist (unsupported)", "yellow")
            else:
                self._log(f"[{symbol}] Temporarily blacklisted due to maker failure.", "yellow")

    async def _handle_waiting_maker(self, ctx: TradeContext):
        try:
            positions = await ctx.maker_ex.get_positions()
            pos = next((p for p in positions if p.symbol == ctx.symbol), None)
            
            if pos:
                self._log(f"[{ctx.symbol}] Maker order FILLED. Firing Taker...", "cyan")
                taker_price = await ctx.taker_ex.get_price(ctx.symbol)
                amount = getattr(ctx, 'target_size_usd', self.target_size_usd) / taker_price
                
                await ctx.taker_ex.open_position(ctx.symbol, ctx.taker_side, amount, order_type='market')
                ctx.state = StrategyState.HEDGED
                self._log(f"[{ctx.symbol}] Fully Hedged.", "green")
                
                # Increment session volume (Taker side)
                self.current_session_volume += getattr(ctx, 'target_size_usd', self.target_size_usd)
            else:
                # Check for timeout (e.g., 30 seconds)
                if time.time() - getattr(ctx, 'maker_order_time', 0.0) > 30.0:
                    self._log(f"[{ctx.symbol}] Maker order timed out. Blacklisting symbol temporarily.", "yellow")
                    self.blacklisted_symbols.add(ctx.symbol)
                    del self.trades[ctx.symbol]
        except Exception as e:
            self.logger.error(f"[{ctx.symbol}] Waiting Maker Error: {e}")

    async def _handle_hedged(self, ctx: TradeContext):
        try:
            positions_a, positions_b = await asyncio.gather(
                self.ex_a.get_positions(),
                self.ex_b.get_positions()
            )
            pos_a = next((p for p in positions_a if p.symbol == ctx.symbol), None)
            pos_b = next((p for p in positions_b if p.symbol == ctx.symbol), None)

            if not pos_a or not pos_b: return

            ctx.current_pnl = pos_a.unrealized_pnl + pos_b.unrealized_pnl
            
            tp = getattr(ctx, 'target_tp_usd', self.take_profit_usd)
            if ctx.current_pnl >= tp:
                self._log(f"[{ctx.symbol}] Target profit reached (${ctx.current_pnl:.2f}). Closing.", "yellow")
                ctx.state = StrategyState.CLOSING
        except Exception as e:
            self.logger.error(f"[{ctx.symbol}] Hedged Error: {e}")

    async def _handle_closing(self, ctx: TradeContext):
        self._log(f"[{ctx.symbol}] Closing all positions...", "yellow")
        try:
            await asyncio.gather(
                self.ex_a.close_position(ctx.symbol),
                self.ex_b.close_position(ctx.symbol)
            )
            self._log(f"[{ctx.symbol}] Positions CLOSED.", "green")
            
            # Increment session volume (Closing both sides)
            trade_size = getattr(ctx, 'target_size_usd', self.target_size_usd)
            self.current_session_volume += (trade_size * 2)
            
            # Remove from active trades
            del self.trades[ctx.symbol]
        except Exception as e:
            self.logger.error(f"[{ctx.symbol}] Closing Error: {e}")
