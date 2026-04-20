"""
hollerback entrypoint.

Usage:
    uv run main.py --account +1XXXXXXXXXX
"""

import argparse
import asyncio
import logging
import signal

from src.hollerback.gateway import Gateway


def main():
    parser = argparse.ArgumentParser(description="Signal → Goose gateway")
    parser.add_argument(
        "--account",
        required=True,
        help="Signal phone number this instance is registered as (e.g. +16125551234)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    gateway = Gateway(
        signal_account=args.account,
    )

    loop = asyncio.new_event_loop()

    def _shutdown(*_):
        print("\nShutting down...")
        loop.create_task(gateway.stop())
        loop.stop()

    loop.add_signal_handler(signal.SIGINT, _shutdown)
    loop.add_signal_handler(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(gateway.start())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
