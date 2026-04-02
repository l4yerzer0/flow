import asyncio
from decimal import Decimal
from typing import List
from src.core.config import (
    GlobalConfig,
    AccountConfig,
    ExchangeConfig,
    StrategySettings,
    SettingsProfile,
)
from src.exchanges.base import ExchangeBase
from src.exchanges.pacifica import PacificaExchange
from src.exchanges.variational import VariationalExchange
from src.exchanges.market_universe import build_common_market_universe
from src.strategy.delta_neutral import DeltaNeutralStrategy, StrategyState

def create_exchange(config: ExchangeConfig, account_name: str, index: int, proxy: str | None = None) -> ExchangeBase:
    """Factory to create exchange instances based on config."""
    name = f"{config.exchange_type.capitalize()} {index} ({account_name})"

    if config.exchange_type == "pacifica":
        return PacificaExchange(
            name=name,
            api_key=config.params.get("public_key", ""),
            api_secret=config.params.get("private_key", ""),
            subaccount_id="0", # Default as requested
            proxy=proxy
        )

    if config.exchange_type == "variational":
        return VariationalExchange(
            name=name,
            api_key=config.params.get("public_key", "") or config.params.get("address", ""),
            api_secret=config.params.get("private_key", ""),
            proxy=proxy
        )

    raise ValueError(f"Unsupported exchange type: {config.exchange_type}")

class BotInstance:
    """Represents a single running strategy (one account)."""
    def __init__(self, config: AccountConfig, settings: StrategySettings):
        self.config = config
        self.settings = settings
        self.ex_type_a = config.exchanges[0].exchange_type if len(config.exchanges) > 0 else ""
        self.ex_type_b = config.exchanges[1].exchange_type if len(config.exchanges) > 1 else ""

        # We require exactly two real exchange configs.
        if len(config.exchanges) < 2:
            raise ValueError(f"Account '{config.name}' must have 2 exchanges configured")

        self.ex_a = create_exchange(config.exchanges[0], config.name, 1, proxy=config.proxy)
        self.ex_b = create_exchange(config.exchanges[1], config.name, 2, proxy=config.proxy)
        
        # Balance Cache
        self.bal_a = Decimal("0.0")
        self.bal_b = Decimal("0.0")
        self.last_bal_update = 0.0
        self.assets_a: list[str] = []
        self.assets_b: list[str] = []
        self.common_assets: list[str] = []
        self.last_markets_update = 0.0
        
        # Statistics Cache
        self.points_a = Decimal("0.0")
        self.points_b = Decimal("0.0")
        self.vols_a = {"24h": Decimal("0.0"), "all_time": Decimal("0.0")}
        self.vols_b = {"24h": Decimal("0.0"), "all_time": Decimal("0.0")}
        self.last_stats_update = 0.0

        self.strategy = DeltaNeutralStrategy(self.ex_a, self.ex_b)
        self.strategy.target_size_usd = Decimal(str(settings.target_size_usd))
        
        # Strategy Parameters from config
        if hasattr(settings, 'min_spread_bps'):
            self.strategy.min_spread_bps = Decimal(str(settings.min_spread_bps))
        else:
            self.strategy.min_spread_bps = Decimal("15.0")
            
        if hasattr(settings, 'max_concurrent_trades'):
            self.strategy.max_concurrent_trades = settings.max_concurrent_trades
        else:
            self.strategy.max_concurrent_trades = 1
            
        if hasattr(settings, 'target_session_volume'):
            self.strategy.target_session_volume = Decimal(str(settings.target_session_volume))
        if hasattr(settings, 'balance_percent'):
            self.strategy.balance_percent = Decimal(str(settings.balance_percent))
        if hasattr(settings, 'min_position_size'):
            self.strategy.min_position_size = Decimal(str(settings.min_position_size))
            
        # Give strategy access to balances
        self.strategy.get_balance_a = lambda: self.bal_a
        self.strategy.get_balance_b = lambda: self.bal_b
        
        # Give strategy a way to stop the bot when session volume is reached
        self.strategy.stop_bot_callback = lambda: asyncio.create_task(self.stop())
            
        # Set a realistic take profit based on target size
        self.strategy.take_profit_usd = self.strategy.target_size_usd * Decimal("0.002") # 0.2% default
        
        self.running = False
        self.task: asyncio.Task = None

    def set_log_callback(self, callback):
        self.strategy.log_callback = callback

    async def update_balances(self, force=False):
        """Smart update balances: fast if trading, slow if idle/disabled."""
        import time
        now = time.time()
        
        # Interval: 1s if active, 600s (10m) if idle or disabled
        interval = 1.0 if (self.running and self.strategy.state != StrategyState.IDLE) else 600.0
        
        if force or (now - self.last_bal_update > interval):
            # No try-except here, let errors bubble up to UI update loop
            self.bal_a, self.bal_b = await asyncio.gather(
                self.ex_a.get_balance(),
                self.ex_b.get_balance()
            )
            self.last_bal_update = now

    async def update_statistics(self, force=False):
        """Update points and volumes (run every 10m)."""
        import time
        now = time.time()
        
        interval = 600.0 # 10m
        
        if force or (now - self.last_stats_update > interval):
            try:
                self.points_a, self.points_b, self.vols_a, self.vols_b = await asyncio.gather(
                    self.ex_a.get_points(),
                    self.ex_b.get_points(),
                    self.ex_a.get_volumes(),
                    self.ex_b.get_volumes()
                )
                self.last_stats_update = now
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to update stats for {self.config.name}: {e}")

    async def update_market_universe(
        self,
        force: bool = False,
        shared_markets_by_exchange: dict[str, list[str]] | None = None,
    ):
        """Refresh available markets and intersection for hedgeable assets."""
        import time
        now = time.time()
        interval = 300.0  # 5 min
        if not force and now - self.last_markets_update <= interval:
            return

        if shared_markets_by_exchange is not None:
            assets_a = shared_markets_by_exchange.get(self.ex_type_a, [])
            assets_b = shared_markets_by_exchange.get(self.ex_type_b, [])
            self.assets_a = sorted(set(assets_a))
            self.assets_b = sorted(set(assets_b))
            self.common_assets = sorted(set(self.assets_a).intersection(self.assets_b))
            self.last_markets_update = now
            return

        universe = await build_common_market_universe(self.ex_a, self.ex_b)
        self.assets_a = universe.exchange_a_assets
        self.assets_b = universe.exchange_b_assets
        self.common_assets = universe.common_assets
        self.last_markets_update = now

    async def start(self, shared_markets_by_exchange: dict[str, list[str]] | None = None):
        if self.running: return
        self.running = True
        
        # Connect exchanges
        await self.ex_a.connect()
        await self.ex_b.connect()
        await self.update_market_universe(force=True, shared_markets_by_exchange=shared_markets_by_exchange)

        # Pass available markets to the strategy
        self.strategy.available_symbols = [f"{a}-PERP" for a in self.common_assets]
        
        # Start Strategy Loop
        self.task = asyncio.create_task(self.strategy.run_loop())

    async def stop(self):
        if not self.running: return
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

        # Clean up exchange connections
        try:
            await self.ex_a.disconnect()
            await self.ex_b.disconnect()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Error disconnecting exchanges for {self.config.name}: {e}")

class BotManager:
    """Manages all bot instances."""
    def __init__(self, config_path: str = "config/accounts.json"):
        self.config_path = config_path
        self.config = GlobalConfig.load(config_path)
        self.bots: List[BotInstance] = []
        self.shared_markets_by_exchange: dict[str, list[str]] = {}
        self.shared_markets_updated_at = 0.0
        self.shared_markets_ttl_sec = 300.0
        self._shared_markets_lock = asyncio.Lock()
        self.log_callback = None
        self._ensure_default_profiles()

        self._initialize_bots()
        
    def set_log_callback(self, callback):
        self.log_callback = callback
        for bot in self.bots:
            bot.set_log_callback(lambda msg, color="white": self.log_callback(f"[{bot.config.name}] {msg}", color) if self.log_callback else None)

    def _ensure_default_profiles(self):
        if self.config.settings_profiles:
            return

        self.config.settings_profiles = [
            SettingsProfile(
                id="default",
                name="Default",
                settings=StrategySettings(
                    target_size_usd=1000.0,
                ),
            ),
            SettingsProfile(
                id="alt",
                name="Alt Profile",
                settings=StrategySettings(
                    target_size_usd=2000.0,
                ),
            ),
        ]
        self.config.save()

    def get_profile(self, profile_id: str) -> SettingsProfile:
        return next(
            (p for p in self.config.settings_profiles if p.id == profile_id),
            self.config.settings_profiles[0],
        )

    def get_profile_name(self, profile_id: str) -> str:
        profile = next((p for p in self.config.settings_profiles if p.id == profile_id), None)
        if profile is None:
            return f"{profile_id} (?)"
        return profile.name

    def resolve_account_settings(self, account: AccountConfig) -> StrategySettings:
        profile = self.get_profile(account.settings_profile_id)
        return account.settings_override.apply_to(profile.settings)

    def _initialize_bots(self):
        self.bots = []
        for acc in self.config.accounts:
            try:
                # We create instances for ALL accounts to track balances
                self.bots.append(BotInstance(acc, self.resolve_account_settings(acc)))
            except Exception as e:
                print(f"Failed to initialize bot for {acc.name}: {e}")

    async def start_account(self, index: int):
        if 0 <= index < len(self.bots):
            bot = self.bots[index]
            shared_markets = await self.get_shared_markets(force=True)
            await bot.start(shared_markets)

    async def stop_account(self, index: int):
        if 0 <= index < len(self.bots):
            await self.bots[index].stop()

    async def start_all(self):
        # Only start the strategy loop for enabled accounts
        shared_markets = await self.get_shared_markets(force=True)
        tasks = [bot.start(shared_markets) for bot in self.bots if bot.config.enabled]
        if tasks:
            await asyncio.gather(*tasks)

    async def get_shared_markets(self, force: bool = False) -> dict[str, list[str]]:
        """Fetch market lists once per exchange type and reuse for all accounts."""
        import time
        now = time.time()
        if (
            not force
            and self.shared_markets_by_exchange
            and now - self.shared_markets_updated_at <= self.shared_markets_ttl_sec
        ):
            return self.shared_markets_by_exchange

        async with self._shared_markets_lock:
            now = time.time()
            if (
                not force
                and self.shared_markets_by_exchange
                and now - self.shared_markets_updated_at <= self.shared_markets_ttl_sec
            ):
                return self.shared_markets_by_exchange

            representatives: dict[str, ExchangeBase] = {}
            for bot in self.bots:
                if bot.ex_type_a and bot.ex_type_a not in representatives:
                    representatives[bot.ex_type_a] = bot.ex_a
                if bot.ex_type_b and bot.ex_type_b not in representatives:
                    representatives[bot.ex_type_b] = bot.ex_b

            if not representatives:
                self.shared_markets_by_exchange = {}
                self.shared_markets_updated_at = now
                return self.shared_markets_by_exchange

            exchange_types = list(representatives.keys())
            calls = [representatives[ex_type].get_markets() for ex_type in exchange_types]
            results = await asyncio.gather(*calls, return_exceptions=True)

            cache: dict[str, list[str]] = {}
            for ex_type, result in zip(exchange_types, results):
                if isinstance(result, Exception):
                    print(f"Failed to fetch markets for exchange '{ex_type}': {result}")
                    continue
                cache[ex_type] = sorted(set(result))

            self.shared_markets_by_exchange = cache
            self.shared_markets_updated_at = time.time()
            return self.shared_markets_by_exchange

    async def stop_all(self):
        tasks = [bot.stop() for bot in self.bots]
        if tasks:
            await asyncio.gather(*tasks)

    async def reload_all(self):
        """Reload all bot instances after config/profile changes."""
        await self.stop_all()
        self._initialize_bots()
        await self.start_all()

    def add_profile(self, profile: SettingsProfile):
        if any(p.id == profile.id for p in self.config.settings_profiles):
            raise ValueError(f"Profile id '{profile.id}' already exists")
        self.config.settings_profiles.append(profile)
        self.config.save()
        asyncio.create_task(self.reload_all())

    def update_profile(self, profile_id: str, updated: SettingsProfile):
        idx = next((i for i, p in enumerate(self.config.settings_profiles) if p.id == profile_id), None)
        if idx is None:
            raise ValueError("Profile not found")

        if updated.id != profile_id and any(p.id == updated.id for p in self.config.settings_profiles):
            raise ValueError(f"Profile id '{updated.id}' already exists")

        self.config.settings_profiles[idx] = updated
        for account in self.config.accounts:
            if account.settings_profile_id == profile_id:
                account.settings_profile_id = updated.id

        self.config.save()
        asyncio.create_task(self.reload_all())

    def remove_profile(self, profile_id: str):
        if len(self.config.settings_profiles) <= 1:
            raise ValueError("At least one profile must remain")

        idx = next((i for i, p in enumerate(self.config.settings_profiles) if p.id == profile_id), None)
        if idx is None:
            raise ValueError("Profile not found")

        remaining = [p for p in self.config.settings_profiles if p.id != profile_id]
        fallback_profile_id = remaining[0].id

        for account in self.config.accounts:
            if account.settings_profile_id == profile_id:
                account.settings_profile_id = fallback_profile_id

        self.config.settings_profiles.pop(idx)
        self.config.save()
        asyncio.create_task(self.reload_all())

    def add_account(self, account: AccountConfig):
        self.config.accounts.append(account)
        self.config.save()
        # Create and start new bot instance
        if account.enabled:
            try:
                new_bot = BotInstance(account, self.resolve_account_settings(account))
                self.bots.append(new_bot)
                asyncio.create_task(self._start_bot_with_shared_markets(new_bot))
            except Exception as e:
                print(f"Failed to add bot for {account.name}: {e}")

    def remove_account(self, index: int):
        if 0 <= index < len(self.config.accounts):
            acc_to_remove = self.config.accounts[index]
            bot_to_remove = next((b for b in self.bots if b.config == acc_to_remove), None)
            
            if bot_to_remove:
                asyncio.create_task(bot_to_remove.stop())
                self.bots.remove(bot_to_remove)
            
            self.config.accounts.pop(index)
            self.config.save()

    def update_account(self, index: int, new_config: AccountConfig):
        if 0 <= index < len(self.config.accounts):
            old_config = self.config.accounts[index]
            self.config.accounts[index] = new_config
            self.config.save()
            
            # Restart bot if config changed significantly
            bot_idx = next((i for i, b in enumerate(self.bots) if b.config == old_config), None)
            if bot_idx is not None:
                asyncio.create_task(self._restart_bot(bot_idx, new_config))
            elif new_config.enabled:
                # If bot wasn't running (e.g. error before), try starting it now
                try:
                    new_bot = BotInstance(new_config, self.resolve_account_settings(new_config))
                    self.bots.append(new_bot)
                    asyncio.create_task(self._start_bot_with_shared_markets(new_bot))
                except Exception:
                    pass

    async def _restart_bot(self, bot_idx: int, new_config: AccountConfig):
        old_bot = self.bots[bot_idx]
        await old_bot.stop()
        try:
            new_bot = BotInstance(new_config, self.resolve_account_settings(new_config))
            self.bots[bot_idx] = new_bot
            shared_markets = await self.get_shared_markets(force=True)
            await new_bot.start(shared_markets)
        except Exception as e:
            print(f"Failed to restart bot: {e}")
            self.bots.pop(bot_idx)

    async def _start_bot_with_shared_markets(self, bot: BotInstance):
        shared_markets = await self.get_shared_markets(force=True)
        await bot.start(shared_markets)
