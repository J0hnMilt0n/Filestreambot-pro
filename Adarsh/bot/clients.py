
# (c) adarsh-goel

import asyncio
import logging

from ..vars import Var
from pyrogram import Client
from Adarsh.utils.config_parser import TokenParser
from . import multi_clients, work_loads, StreamBot


async def initialize_clients():

    # Register the main bot
    multi_clients.clear()
    work_loads.clear()

    multi_clients[0] = StreamBot
    work_loads[0] = 0

    all_tokens = TokenParser().parse_from_env()

    if not all_tokens:
        print("No additional clients found, using default client")
        return

    async def start_client(client_id, token):

        client = None

        try:
            print(f"Starting - Client {client_id}")

            if client_id == len(all_tokens):
                await asyncio.sleep(2)
                print("This will take some time, please wait...")

            client = Client(
                name=str(client_id),
                api_id=Var.API_ID,
                api_hash=Var.API_HASH,
                bot_token=token,
                sleep_threshold=Var.SLEEP_THRESHOLD,
                no_updates=True,
                in_memory=True
            )

            await client.start()

            print(f"Client {client_id} started successfully")

            return client_id, client

        except Exception:
            logging.exception(
                f"Failed starting Client - {client_id}"
            )

            if client:
                try:
                    await client.stop()
                except Exception:
                    pass

            return None

    results = await asyncio.gather(
        *[
            start_client(i, token)
            for i, token in all_tokens.items()
        ],
        return_exceptions=True
    )

    started = 0

    for result in results:

        if isinstance(result, tuple):
            client_id, client = result

            multi_clients[client_id] = client
            work_loads[client_id] = 0

            started += 1

    if started:
        Var.MULTI_CLIENT = True
        print(f"Multi-Client Mode Enabled: {started} additional clients")
    else:
        Var.MULTI_CLIENT = False
        print("No additional clients were initialized")
        print("Using default client")
