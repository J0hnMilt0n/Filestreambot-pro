
# (c) adarsh-goel

import asyncio
import logging

from telethon import TelegramClient
from telethon.sessions import MemorySession
from telethon.errors import RpcCallFailError
import TelethonFakeTLS

# Monkey-patch TelethonFakeTLS to add missing wait_closed method
# This ensures the patch is applied when the connection is actually used
try:
    from TelethonFakeTLS.FakeTLS.TLSInOut import FakeTLSStreamWriter
    if not hasattr(FakeTLSStreamWriter, 'wait_closed'):
        async def _wait_closed(self):
            if hasattr(self.upstream, 'wait_closed'):
                await self.upstream.wait_closed()
        FakeTLSStreamWriter.wait_closed = _wait_closed
        logging.getLogger(__name__).info("Patched FakeTLSStreamWriter with wait_closed method")
except ImportError:
    pass

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
    error_msg = str(error).lower()
    # Check for Telegram internal issues in any exception message
    if "telegram is having internal issues" in error_msg:
        return True
    # Also check for RpcCallFailError specifically
    if isinstance(error, RpcCallFailError):
        return "telegram is having internal issues" in error_msg
    # Check common Telegram error patterns
    if any(keyword in error_msg for keyword in [
        "internal issues",
        "try again later",
        "rpc call fail",
        "importbotauthorizationrequest",
    ]):
        return True
    return False


async def initialize_clients():

    multi_clients.clear()
    work_loads.clear()

    # Start main bot (single attempt - retries handled by caller)
    if not StreamBot.is_connected():
        try:
            logging.info("Starting main bot...")
            await StreamBot.start(bot_token=Var.BOT_TOKEN)
            logging.info("Main bot started successfully")
        except Exception as e:
            logging.warning("Main bot startup failed: %s", e)
            await StreamBot.disconnect()
            raise

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

        try:
            logging.info("Starting client %s...", client_id)
            client = create_client(MemorySession())
            await client.start(bot_token=token)
            multi_clients[client_id] = client
            work_loads[client_id] = 0
            started += 1
            logging.info("Client %s started successfully", client_id)
            client = None
        except Exception as e:
            logging.warning("Client %s failed: %s", client_id, e)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        await asyncio.sleep(3)

    Var.MULTI_CLIENT = started > 0

    logging.info(
        "Multi-client mode: %s",
        "enabled" if started else "disabled"
    )
