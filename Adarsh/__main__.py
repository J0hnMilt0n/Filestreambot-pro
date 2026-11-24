
# (c) adarsh-goel

import os
import sys
import glob
import asyncio
import logging
import importlib.util

from pathlib import Path
from pyrogram import idle
from pyrogram.errors import RPCError
from aiohttp import web

from .bot import StreamBot
from .vars import Var
from .server import web_server
from .utils.keepalive import ping_server
from Adarsh.bot.clients import initialize_clients


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)

logging.info("Starting DL Upload Host...")


# Plugin directory
PLUGIN_DIR = Path(__file__).resolve().parent / "bot" / "plugins"


async def start_bot():
    """Start Telegram bot with limited retries."""

    for attempt in range(1, 6):
        try:
            logging.info(
                f"Starting Telegram bot (attempt {attempt}/5)"
            )

            await StreamBot.start()

            logging.info("Telegram bot started successfully")
            return

        except Exception as e:
            error_text = str(e).lower()
            error_code = getattr(e, "CODE", None)

            temporary_error = (
                isinstance(e, (asyncio.TimeoutError, OSError))
                or (isinstance(e, RPCError) and error_code is not None
                    and error_code >= 500)
                or "timeout" in error_text
                or "rpc_call_fail" in error_text
            )

            if not temporary_error or attempt == 5:
                logging.exception("Telegram bot startup failed")
                raise

            wait_time = attempt * 5

            logging.warning(
                f"Temporary Telegram error: {e}. "
                f"Retrying in {wait_time} seconds..."
            )

            await asyncio.sleep(wait_time)


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
                f"Failed to load plugin: {plugin_name}"
            )
            raise

    print("------------------------------ DONE ------------------------------")


async def start_services():

    app_runner = None
    ping_task = None
    bot_started = False

    try:

        print("\n")
        print("------------------- Initializing Telegram Bot -------------------")

        await start_bot()
        bot_started = True

        bot_info = await StreamBot.get_me()
        StreamBot.username = bot_info.username

        print("------------------------------ DONE ------------------------------")
        print()

        print("---------------------- Initializing Clients ----------------------")

        await initialize_clients()

        print("------------------------------ DONE ------------------------------")
        print()

        # Import plugins
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

        bot_info = await StreamBot.get_me()

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

        await idle()

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

        if bot_started:
            try:
                await StreamBot.stop()
                logging.info("Telegram bot stopped")
            except Exception:
                logging.exception("Telegram bot shutdown error")

        logging.info("Shutdown completed")


if __name__ == "__main__":

    try:
        asyncio.run(start_services())

    except KeyboardInterrupt:
        logging.info("Service stopped by user")

    except Exception:
        logging.exception("Application exited with an error")
