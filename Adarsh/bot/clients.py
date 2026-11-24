
# (c) adarsh-goel

import asyncio
import logging

from ..vars import Var
from pyrogram import Client
from Adarsh.utils.config_parser import TokenParser
from . import multi_clients, work_loads, StreamBot


async def initialize_clients():

    multi_clients.clear()
    work_loads.clear()

    # Register main bot
    multi_clients[0] = StreamBot
    work_loads[0] = 0

    all_tokens = TokenParser().parse_from_env()

    if not all_tokens:
        Var.MULTI_CLIENT = False
        print("No additional clients found, using default client")
        return

    started = 0

    # Start clients sequentially
    for client_id, token in all_tokens.items():

        client = None

        for attempt in range(1, 6):

            try:
                print(
                    f"Starting Client {client_id} "
                    f"(attempt {attempt}/5)"
                )

                client = Client(
                    name=f"multi_{client_id}",
                    api_id=Var.API_ID,
                    api_hash=Var.API_HASH,
                    bot_token=token,
                    sleep_threshold=Var.SLEEP_THRESHOLD,
                    no_updates=True,
                    in_memory=True
                )

                await client.start()

                multi_clients[client_id] = client
                work_loads[client_id] = 0

                started += 1

                print(
                    f"Client {client_id} started successfully"
                )

                client = None
                break

            except Exception as e:

                logging.warning(
                    "Client %s startup failed "
                    "(attempt %s/5): %s",
                    client_id,
                    attempt,
                    e
                )

                if client is not None:
                    try:
                        await client.stop()
                    except Exception:
                        pass

                    client = None

                if attempt < 5:
                    delay = min(5 * (2 ** (attempt - 1)), 60)

                    logging.info(
                        "Retrying Client %s in %s seconds",
                        client_id,
                        delay
                    )

                    await asyncio.sleep(delay)

        # Give Telegram time between clients
        await asyncio.sleep(3)

    Var.MULTI_CLIENT = started > 0

    if started:
        print(
            f"Multi-Client Mode Enabled: "
            f"{started} additional clients"
        )
    else:
        print("No additional clients initialized")
        print("Using default client")
