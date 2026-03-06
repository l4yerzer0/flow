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
from src.strategy.delta_neutral import DeltaNeutralStrategy, StrategyState

def create_exchange(config: ExchangeConfig, account_name: str, index: int) -> ExchangeBase:
    """Factory to create exchange instances based on config."""
    name = f"{config.exchange_type.capitalize()} {index} ({account_name})"

    if config.exchange_type == "pacifica":
        return PacificaExchange(
            name=name,
            api_key=config.params.get("public_key", ""),
            api_secret=config.params.get("private_key", ""),
            subaccount_id="0" # Default as requested
        )

    if config.exchange_type == "variational":
        return VariationalExchange(
            name=name,
            api_key=config.params.get("public_key", "") or config.params.get("address", ""),
            api_secret=config.params.get("private_key", "")
        )

    raise ValueError(f"Unsupported exchange type: {config.exchange_type}")

class BotInstance:
    """Represents a single running strategy (one account)."""
    def __init__(self, config: AccountConfig, settings: StrategySettings):
        self.config = config
        self.settings = settings

        # We require exactly two real exchange configs.
        if len(config.exchanges) < 2:
            raise ValueError(f"Account '{config.name}' must have 2 exchanges configured")

        self.ex_a = create_exchange(config.exchanges[0], config.name, 1)
        self.ex_b = create_exchange(config.exchanges[1], config.name, 2)
        
        # Balance Cache
        self.bal_a = Decimal("0.0")
        self.bal_b = Decimal("0.0")
        self.last_bal_update = 0.0

        if hasattr(self.ex_a, 'balance'): self.ex_a.balance = Decimal("10000.0")
        if hasattr(self.ex_b, 'balance'): self.ex_b.balance = Decimal("10000.0")

        self.strategy = DeltaNeutralStrategy(self.ex_a, self.ex_b)
        self.strategy.target_size_usd = Decimal(str(settings.target_size_usd))
        self.strategy.symbol = settings.symbol
        
        self.running = False
        self.task: asyncio.Task = None

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

    async def start(self):
        if self.running: return
        self.running = True
        
        # Connect exchanges
        await self.ex_a.connect()
        await self.ex_b.connect()
        
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

class BotManager:
    """Manages all bot instances."""
    def __init__(self, config_path: str = "config/accounts.json"):
        self.config_path = config_path
        self.config = GlobalConfig.load(config_path)
        self.bots: List[BotInstance] = []
        self._ensure_default_profiles()

        self._initialize_bots()

    def _ensure_default_profiles(self):
        if self.config.settings_profiles:
            return

        self.config.settings_profiles = [
            SettingsProfile(
                id="default",
                name="Default",
                settings=StrategySettings(
                    target_size_usd=1000.0,
                    symbol="BTC-PERP",
                    max_spread_bps=10.0,
                    rebalance_interval_sec=30,
                ),
            ),
            SettingsProfile(
                id="alt",
                name="Alt Profile",
                settings=StrategySettings(
                    target_size_usd=2000.0,
                    symbol="BTC-PERP",
                    max_spread_bps=20.0,
                    rebalance_interval_sec=15,
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

    async def start_all(self):
        # Only start the strategy loop for enabled accounts
        tasks = [bot.start() for bot in self.bots if bot.config.enabled]
        if tasks:
            await asyncio.gather(*tasks)

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
                asyncio.create_task(new_bot.start())
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
                    asyncio.create_task(new_bot.start())
                except Exception:
                    pass

    async def _restart_bot(self, bot_idx: int, new_config: AccountConfig):
        old_bot = self.bots[bot_idx]
        await old_bot.stop()
        try:
            new_bot = BotInstance(new_config, self.resolve_account_settings(new_config))
            self.bots[bot_idx] = new_bot
            await new_bot.start()
        except Exception as e:
            print(f"Failed to restart bot: {e}")
            self.bots.pop(bot_idx)
