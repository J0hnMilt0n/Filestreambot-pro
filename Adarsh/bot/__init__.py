
# (c) adarsh-goel

import logging

from telethon import TelegramClient
import TelethonFakeTLS

from Adarsh.vars import Var

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Shared multi-client storage
multi_clients = {}
work_loads = {}

# Create main Telegram client
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

# Register main client
multi_clients[0] = StreamBot
work_loads[0] = 0
