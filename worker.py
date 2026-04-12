import asyncio
import logging

from main import run_order_paid_worker

logging.basicConfig(level=logging.INFO)


if __name__ == "__main__":
    asyncio.run(run_order_paid_worker())
