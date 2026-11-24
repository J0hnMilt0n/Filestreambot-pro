
# (c) adarsh-goel

import logging
import pyromod.listen

from pyrogram import Client
from Adarsh.vars import Var


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

StreamBot = Client(
    name="Web Streamer",
    api_id=Var.API_ID,
    api_hash=Var.API_HASH,
    bot_token=Var.BOT_TOKEN,
    sleep_threshold=Var.SLEEP_THRESHOLD,
    workers=Var.WORKERS,
    max_concurrent_transmissions=4
)

multi_clients = {
    0: StreamBot
}

work_loads = {
    0: 0
}
