import logging

import TelethonFakeTLS
from telethon import TelegramClient

from Adarsh.vars import Var

logging.basicConfig(level=logging.INFO)

StreamBot = TelegramClient(
    "Web Streamer",
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
    retry_delay=5
)