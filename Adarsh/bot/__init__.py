
# (c) adarsh-goel

import logging

from telethon import TelegramClient
from telethon.sessions import MemorySession
import TelethonFakeTLS

from Adarsh.vars import Var

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Shared multi-client storage
multi_clients = {}
work_loads = {}

# Create main Telegram client (will use proxy fallback in initialize_clients)
StreamBot = TelegramClient(
    MemorySession(),
    Var.API_ID,
    Var.API_HASH,
    request_retries=2,
    connection_retries=5,
    retry_delay=5
)

# Register main client
multi_clients[0] = StreamBot
work_loads[0] = 0
