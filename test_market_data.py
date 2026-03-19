
import asyncio
import sys
import os
from decimal import Decimal

# Add src to path
sys.path.append(os.getcwd())

from src.exchanges.pacifica import PacificaExchange
from src.exchanges.variational import VariationalExchange

async def test_exchanges():
    print("--- Starting Market Data Verification ---")
    
    # Initialize exchanges (dummy keys are fine for public market data)
    pacifica = PacificaExchange("TestPacifica")
    variational = VariationalExchange("TestVariational")
    
    symbols = ["BTC-PERP", "ETH-PERP", "SUI-PERP", "ARB-PERP"]
    
    for sym in symbols:
        print(f"\n[Testing Symbol: {sym}]")
        
        # Test Pacifica
        try:
            p_price = await pacifica.get_price(sym)
            p_fund = await pacifica.get_funding_rate(sym)
            print(f"Pacifica    -> Price: {p_price}, Funding: {p_fund}")
        except Exception as e:
            print(f"Pacifica    -> ERROR: {e}")
            
        # Test Variational
        try:
            v_price = await variational.get_price(sym)
            v_fund = await variational.get_funding_rate(sym)
            print(f"Variational -> Price: {v_price}, Funding: {v_fund}")
        except Exception as e:
            print(f"Variational -> ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_exchanges())
