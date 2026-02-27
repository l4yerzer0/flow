import asyncio
import logging
from typing import List, Dict
from src.core.config import GlobalConfig, AccountConfig, ExchangeConfig
from src.exchanges.base import ExchangeBase
from src.exchanges.mock import MockExchange
from src.strategy.delta_neutral import DeltaNeutralStrategy, StrategyState

def create_exchange(config: ExchangeConfig, account_name: str, index: int) -> ExchangeBase:
    """Factory to create exchange instances based on config."""
    name = f"{config.exchange_type.capitalize()} {index} ({account_name})"
    # In the future, this will return actual implementations
    # For now, all map to MockExchange but could use different params
    return MockExchange(name)

class BotInstance:
    """Represents a single running strategy (one account)."""
    def __init__(self, config: AccountConfig):
        self.config = config
        
        # Ensure we have at least 2 exchanges configured
        if len(config.exchanges) < 2:
            # Fallback for old configs or incomplete ones
            self.ex_a = MockExchange(f"Mock A ({config.name})")
            self.ex_b = MockExchange(f"Mock B ({config.name})")
        else:
            self.ex_a = create_exchange(config.exchanges[0], config.name, 1)
            self.ex_b = create_exchange(config.exchanges[1], config.name, 2)
        
        # Override mock balance with a realistic initial for demo
        if hasattr(self.ex_a, 'balance'): self.ex_a.balance = 10000.0
        if hasattr(self.ex_b, 'balance'): self.ex_b.balance = 10000.0

        self.strategy = DeltaNeutralStrategy(self.ex_a, self.ex_b)
        self.strategy.target_size_usd = config.target_size_usd
        
        self.running = False
        self.task: asyncio.Task = None

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

        # If no accounts exist, create a default mock one for first run
        if not self.config.accounts:
            default_acc = AccountConfig(
                name="Demo Account", 
                target_size_usd=1000.0,
                exchanges=[
                    ExchangeConfig(exchange_type="mock"),
                    ExchangeConfig(exchange_type="mock")
                ]
            )
            self.config.accounts.append(default_acc)
            self.config.save()

        self._initialize_bots()

    def _initialize_bots(self):
        self.bots = [BotInstance(acc) for acc in self.config.accounts if acc.enabled]

    async def start_all(self):
        tasks = [bot.start() for bot in self.bots]
        if tasks:
            await asyncio.gather(*tasks)

    async def stop_all(self):
        tasks = [bot.stop() for bot in self.bots]
        if tasks:
            await asyncio.gather(*tasks)

    def add_account(self, account: AccountConfig):
        self.config.accounts.append(account)
        self.config.save()
        # Create and start new bot instance
        if account.enabled:
            new_bot = BotInstance(account)
            self.bots.append(new_bot)
            # In a real app we'd await start() but this is sync call usually from UI
            # We'll rely on the UI loop/task to start it or call explicit start
            asyncio.create_task(new_bot.start())

    def remove_account(self, index: int):
        if 0 <= index < len(self.config.accounts):
            acc_to_remove = self.config.accounts[index]
            # Find and stop the bot
            # (Simple linear search by name/config reference)
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
            
            # Restart bot if config changed significantly or just for safety
            # Find old bot
            bot_idx = next((i for i, b in enumerate(self.bots) if b.config == old_config), None)
            if bot_idx is not None:
                old_bot = self.bots[bot_idx]
                asyncio.create_task(self._restart_bot(bot_idx, new_config))

    async def _restart_bot(self, bot_idx: int, new_config: AccountConfig):
        old_bot = self.bots[bot_idx]
        await old_bot.stop()
        new_bot = BotInstance(new_config)
        self.bots[bot_idx] = new_bot
        await new_bot.start()
