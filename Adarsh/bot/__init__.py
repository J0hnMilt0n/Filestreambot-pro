
# (c) adarsh-goel

import logging
from Adarsh.vars import Var
from telethon import TelegramClient, connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

StreamBot = TelegramClient(
    "Web Streamer",
    Var.API_ID,
    Var.API_HASH,
    connection=connection.ConnectionTcpMTProxyRandomizedIntermediate,
    proxy=(
        Var.TELEGRAM_PROXY_HOST,
        Var.TELEGRAM_PROXY_PORT,
        Var.TELEGRAM_PROXY_SECRET
    )
)

async def start_bot():
    await StreamBot.start(bot_token=Var.BOT_TOKEN)

StreamBot.loop.run_until_complete(start_bot())

multi_clients = {
    0: StreamBot
}

work_loads = {
    0: 0
}
