from typing import List, Optional
from pydantic import BaseModel, Field
import json
import os

class ExchangeConfig(BaseModel):
    api_key: str = ""
    api_secret: str = ""
    wallet_private_key: Optional[str] = None # For signing transactions/sessions
    
class AccountConfig(BaseModel):
    name: str = "Account 1"
    enabled: bool = True
    pacifica: ExchangeConfig = Field(default_factory=ExchangeConfig)
    variational: ExchangeConfig = Field(default_factory=ExchangeConfig)
    target_size_usd: float = 1000.0

class GlobalConfig(BaseModel):
    accounts: List[AccountConfig] = []

    @classmethod
    def load(cls, path: str = "config/accounts.json"):
        if not os.path.exists(path):
            return cls() # Return empty default
        try:
            with open(path, "r") as f:
                return cls.model_validate_json(f.read())
        except Exception as e:
            print(f"Error loading config: {e}")
            return cls()

    def save(self, path: str = "config/accounts.json"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(self.model_dump_json(indent=2))
