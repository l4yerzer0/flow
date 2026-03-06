from typing import List, Optional
from pydantic import BaseModel, Field
import os
from src.core.credentials import encrypt_params, decrypt_params

class ExchangeConfig(BaseModel):
    exchange_type: str = "mock"
    params: dict[str, str] = Field(default_factory=dict)
    last_error: Optional[str] = None


class StrategySettings(BaseModel):
    target_size_usd: float = 1000.0
    symbol: str = "BTC-PERP"
    max_spread_bps: float = 10.0
    rebalance_interval_sec: int = 30


class StrategySettingsOverride(BaseModel):
    target_size_usd: Optional[float] = None
    symbol: Optional[str] = None
    max_spread_bps: Optional[float] = None
    rebalance_interval_sec: Optional[int] = None

    def apply_to(self, base: StrategySettings) -> StrategySettings:
        data = base.model_dump()
        patch = self.model_dump(exclude_none=True)
        data.update(patch)
        return StrategySettings(**data)


class SettingsProfile(BaseModel):
    id: str
    name: str
    settings: StrategySettings = Field(default_factory=StrategySettings)


class AccountConfig(BaseModel):
    name: str = "Account 1"
    enabled: bool = True
    exchanges: List[ExchangeConfig] = Field(default_factory=list)
    settings_profile_id: str = "default"
    settings_override: StrategySettingsOverride = Field(default_factory=StrategySettingsOverride)

class GlobalConfig(BaseModel):
    settings_profiles: List[SettingsProfile] = Field(default_factory=list)
    accounts: List[AccountConfig] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str = "config/accounts.json"):
        if not os.path.exists(path):
            return cls() # Return empty default
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                raw = f.read().strip()
                if not raw:
                    return cls()
                config = cls.model_validate_json(raw)
                for account in config.accounts:
                    for exchange in account.exchanges:
                        exchange.params = decrypt_params(exchange.params)
                return config
        except Exception as e:
            print(f"Error loading config: {e}")
            return cls()

    def save(self, path: str = "config/accounts.json"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = self.model_dump()
        for account in data.get("accounts", []):
            for exchange in account.get("exchanges", []):
                params = exchange.get("params", {})
                exchange["params"] = encrypt_params(params)
        with open(path, "w", encoding="utf-8") as f:
            f.write(GlobalConfig.model_validate(data).model_dump_json(indent=2))
