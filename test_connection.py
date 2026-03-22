"""Quick test: connect to DRW exchange, register, and list all symbols + orderbooks."""
import asyncio
import os
import aiohttp
import json
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("DRW_TOKEN", "YOUR_TOKEN_HERE")
GAME_ID = int(os.environ.get("GAME_ID", 160))
BASE_URL = os.environ.get("BASE_URL", "https://games.drw.com")
API_URL = f"{BASE_URL}/api/games/trading-simulator/{GAME_ID}"


async def main():
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        # Try to register
        print("Attempting registration...")
        async with session.post(f"{API_URL}/register") as resp:
            print(f"  Register: {resp.status}")
            if resp.status != 200:
                text = await resp.text()
                print(f"  Response: {text[:500]}")

        # Get account info
        print("\nFetching account...")
        async with session.get(f"{API_URL}/account") as resp:
            print(f"  Account: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                print(f"  Cash: {data.get('cash')}")
                print(f"  Margin: {data.get('margin', 0)}")
                positions = data.get('positions', {})
                print(f"  Positions: {len(positions)} active")
                for sym, qty in list(positions.items())[:5]:
                    print(f"    {sym}: {qty}")

        # Get orderbooks
        print("\nFetching orderbooks...")
        async with session.get(f"{API_URL}/orderbooks") as resp:
            print(f"  Orderbooks: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                symbols = sorted(data.keys())
                print(f"  Found {len(symbols)} symbols:")
                for sym in symbols:
                    book = data[sym]
                    bids = book.get("bids", {})
                    asks = book.get("asks", {})
                    best_bid = max((float(p) for p in bids if bids[p] > 0), default=None)
                    best_ask = min((float(p) for p in asks if asks[p] > 0), default=None)
                    mid = ((best_bid + best_ask) / 2) if best_bid and best_ask else None
                    print(f"    {sym:<30} bid={best_bid}  ask={best_ask}  mid={mid}")
            else:
                text = await resp.text()
                print(f"  Response: {text[:500]}")


asyncio.run(main())
