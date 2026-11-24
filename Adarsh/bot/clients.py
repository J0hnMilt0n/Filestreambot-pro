
# (c) adarsh-goel

import asyncio
import logging

from telethon import TelegramClient
from telethon.sessions import MemorySession
from telethon.errors import RpcCallFailError
import TelethonFakeTLS

from Adarsh.vars import Var
from Adarsh.utils.config_parser import TokenParser
from Adarsh.bot import multi_clients, work_loads, StreamBot


def create_client(session):
    return TelegramClient(
        session,
        Var.API_ID,
        Var.API_HASH,
        connection=TelethonFakeTLS.ConnectionTcpMTProxyFakeTLS,
        proxy=(
            Var.TELEGRAM_PROXY_HOST,
            Var.TELEGRAM_PROXY_PORT,
            Var.TELEGRAM_PROXY_SECRET[2:]
        ),
        request_retries=2,
        connection_retries=5,
        retry_delay=5,
        flood_sleep_threshold=Var.SLEEP_THRESHOLD,
        receive_updates=False
    )


def is_telegram_internal_error(error):
    """Check if the error is a transient Telegram internal issue."""
    if isinstance(error, RpcCallFailError):
        error_msg = str(error).lower()
        return "telegram is having internal issues" in error_msg
    return False


async def initialize_clients():

    multi_clients.clear()
    work_loads.clear()

    # Start main bot
    if not StreamBot.is_connected():
        for attempt in range(1, 6):
            try:
                logging.info(
                    "Starting main bot (attempt %s/5)",
                    attempt
                )

                await StreamBot.start(
                    bot_token=Var.BOT_TOKEN
                )

                logging.info("Main bot started successfully")
                break

            except Exception as e:
                logging.warning(
                    "Main bot startup failed: %s", e
                )

                await StreamBot.disconnect()

                if attempt == 5:
                    raise

                # Use longer delay for Telegram internal issues
                if is_telegram_internal_error(e):
                    delay = min(30 * (2 ** (attempt - 1)), 120)
                    logging.info(
                        "Telegram internal issue detected, waiting %ss before retry",
                        delay
                    )
                else:
                    delay = min(5 * (2 ** (attempt - 1)), 60)
                
                await asyncio.sleep(delay)

    multi_clients[0] = StreamBot
    work_loads[0] = 0

    # Additional bot clients
    all_tokens = TokenParser().parse_from_env()

    if not all_tokens:
        Var.MULTI_CLIENT = False
        logging.info("No additional clients found")
        return

    started = 0

    for client_id, token in all_tokens.items():

        client = None

        for attempt in range(1, 6):

            try:
                logging.info(
                    "Starting client %s (attempt %s/5)",
                    client_id,
                    attempt
                )

                client = create_client(MemorySession())

                await client.start(bot_token=token)

                multi_clients[client_id] = client
                work_loads[client_id] = 0

                started += 1

                logging.info(
                    "Client %s started successfully",
                    client_id
                )

                client = None
                break

            except Exception as e:

                logging.warning(
                    "Client %s failed (attempt %s/5): %s",
                    client_id,
                    attempt,
                    e
                )

                if client is not None:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

                if attempt < 5:
                    # Use longer delay for Telegram internal issues
                    if is_telegram_internal_error(e):
                        delay = min(30 * (2 ** (attempt - 1)), 120)
                        logging.info(
                            "Telegram internal issue detected, waiting %ss before retry",
                            delay
                        )
                    else:
                        delay = min(5 * (2 ** (attempt - 1)), 60)
                    
                    await asyncio.sleep(delay)

        await asyncio.sleep(3)

    Var.MULTI_CLIENT = started > 0

    logging.info(
        "Multi-client mode: %s",
        "enabled" if started else "disabled"
    )
