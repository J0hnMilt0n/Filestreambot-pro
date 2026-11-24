
# (c) adarsh-goel

import sys
import asyncio
import logging
import importlib.util

from pathlib import Path
from aiohttp import web

from .bot import StreamBot, multi_clients, work_loads
from .bot.clients import initialize_clients
from .vars import Var
from .server import web_server
from .utils.keepalive import ping_server


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)
logging.getLogger("telethon").setLevel(logging.INFO)

logging.info("Starting DL Upload Host...")

PLUGIN_DIR = Path(__file__).resolve().parent / "bot" / "plugins"


async def load_plugins():
    """Load all bot plugins."""

    print("\n")
    print("--------------------------- Importing Plugins ---------------------------")

    if not PLUGIN_DIR.exists():
        logging.warning("Plugin directory does not exist")
        return

    files = sorted(PLUGIN_DIR.glob("*.py"))

    for plugin_file in files:

        if plugin_file.name.startswith("_"):
            continue

        plugin_name = plugin_file.stem
        module_name = f"Adarsh.bot.plugins.{plugin_name}"

        try:
            spec = importlib.util.spec_from_file_location(
                module_name,
                plugin_file
            )

            if spec is None or spec.loader is None:
                raise ImportError(
                    f"Cannot load plugin: {plugin_name}"
                )

            module = importlib.util.module_from_spec(spec)

            sys.modules[module_name] = module

            spec.loader.exec_module(module)

            print(f"Imported => {plugin_name}")

        except Exception:
            logging.exception(
                "Failed to load plugin: %s",
                plugin_name
            )
            raise

    print("------------------------------ DONE ------------------------------")


async def start_services():

    app_runner = None
    ping_task = None

    try:

        # Initialize Telegram clients
        print("\n")
        print("------------------- Initializing Telegram Bot -------------------")

        await initialize_clients()

        print("------------------------------ DONE ------------------------------")
        print()

        bot_info = await StreamBot.get_me()
        StreamBot.username = bot_info.username

        # Initialize plugins
        await load_plugins()

        # Keep Alive
        if Var.ON_HEROKU:

            print("------------------ Starting Keep Alive Service ------------------")

            ping_task = asyncio.create_task(ping_server())

        # Start web server
        print("-------------------- Initializing Web Server --------------------")

        app = await web_server()

        app_runner = web.AppRunner(app)

        await app_runner.setup()

        bind_address = (
            "0.0.0.0"
            if Var.ON_HEROKU
            else Var.BIND_ADRESS
        )

        site = web.TCPSite(
            app_runner,
            bind_address,
            Var.PORT
        )

        await site.start()

        print("----------------------------- DONE -----------------------------")
        print()

        print("------------------------------------------------------------------")
        print("Follow: https://github.com/adarsh-goel")
        print("------------------------------------------------------------------")
        print()

        print("----------------------- Service Started -------------------------")

        print(f"Bot        =>> {bot_info.first_name}")
        print(f"Server IP  =>> {bind_address}:{Var.PORT}")
        print(f"Owner      =>> {Var.OWNER_USERNAME}")

        if Var.ON_HEROKU:
            print(f"App URL    =>> {Var.FQDN}")

        print("------------------------------------------------------------------")

        print(
            "Give a star: "
            "https://github.com/adarsh-goel/filestreambot-pro"
        )

        print("------------------------------------------------------------------")

        # Keep the application alive
        await asyncio.Event().wait()

    except asyncio.CancelledError:

        logging.info("Service cancellation received")
        raise

    except Exception:

        logging.exception("Application startup/runtime failed")
        raise

    finally:

        logging.info("Shutting down services...")

        if ping_task:
            ping_task.cancel()

            try:
                await ping_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logging.exception("Keepalive shutdown error")

        if app_runner:
            await app_runner.cleanup()

        # Disconnect all Telethon clients
        for client_id, client in list(multi_clients.items()):

            try:
                if client.is_connected():
                    await client.disconnect()

                logging.info(
                    "Telegram client %s disconnected",
                    client_id
                )

            except Exception:
                logging.exception(
                    "Failed to disconnect client %s",
                    client_id
                )

        multi_clients.clear()
        work_loads.clear()

        logging.info("Shutdown completed")


if __name__ == "__main__":

    try:
        asyncio.run(start_services())

    except KeyboardInterrupt:
        logging.info("Service stopped by user")

    except Exception:
        logging.exception("Application exited with an error")
