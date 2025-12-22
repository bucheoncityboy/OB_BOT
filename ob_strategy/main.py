import asyncio
import logging
from config import API_KEY, API_SECRET, DEFAULT_CONFIG
from bot import TradingBot

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - [ASYNC] - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )

    try:
        bot = TradingBot(API_KEY, API_SECRET, DEFAULT_CONFIG)
        await bot.run_async()
    except Exception as e:
        logging.critical(f"Fatal Error: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
