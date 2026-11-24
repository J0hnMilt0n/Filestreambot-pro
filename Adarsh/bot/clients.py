
# (c) adarsh-goel

import logging

from Adarsh.vars import Var
from telethon import TelegramClient, connection
from telethon.sessions import MemorySession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

def create_telegram_client(session):
    return TelegramClient(
        session,
        Var.API_ID,
        Var.API_HASH,
        connection=connection.ConnectionTcpMTProxyRandomizedIntermediate,
        proxy=(
            Var.TELEGRAM_PROXY_HOST,
            Var.TELEGRAM_PROXY_PORT,
            Var.TELEGRAM_PROXY_SECRET
        ),
        flood_sleep_threshold=Var.SLEEP_THRESHOLD,
        request_retries=5,
        connection_retries=5,
        retry_delay=2
    )


StreamBot = create_telegram_client("Web Streamer")

multi_clients = {
    0: StreamBot
}

work_loads = {
    0: 0
}
