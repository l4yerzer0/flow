import asyncio
import json
from src.core.config import GlobalConfig
from src.exchanges.pacifica import PacificaExchange

async def main():
    conf = GlobalConfig.load('config/accounts.json')
    p_conf = conf.accounts[0].exchanges[0]
    ex = PacificaExchange('test', p_conf.params.get('public_key', ''), p_conf.params.get('private_key', ''))
    await ex.connect()
    try:
        r = await ex._request('GET', '/positions', {'account': ex.api_key}, sign_type='get_positions')
        print(json.dumps(r, indent=2))
    except Exception as e:
        print(e)
    finally:
        await ex.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
