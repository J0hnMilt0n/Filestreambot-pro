
# (c) adarsh-goel

import sys
import asyncio
import logging
import importlib.util

# Monkey-patch TelethonFakeTLS to add missing wait_closed method
# This must be done before importing any telethon modules
try:
    from TelethonFakeTLS.FakeTLS.TLSInOut import FakeTLSStreamWriter
    if not hasattr(FakeTLSStreamWriter, 'wait_closed'):
        async def _wait_closed(self):
            if hasattr(self.upstream, 'wait_closed'):
                await self.upstream.wait_closed()
        FakeTLSStreamWriter.wait_closed = _wait_closed
        logging.getLogger(__name__).info("Patched FakeTLSStreamWriter with wait_closed method")
except ImportError:
    pass  # TelethonFakeTLS not installed yet, will be patched on next import

from pathlib import Path
from aiohttp import web

from .bot import StreamBot, multi_clients, work_loads
from .bot.clients import initialize_clients
from .vars import Var
from .server import web_server
from .utils.keepalive import ping_server

# Log masked token for verification (first 10 chars + last 4)
if Var.BOT_TOKEN:
    masked = Var.BOT_TOKEN[:10] + "..." + Var.BOT_TOKEN[-4:] if len(Var.BOT_TOKEN) > 14 else "***"
    logging.info("BOT_TOKEN loaded: %s", masked)
else:
    logging.error("BOT_TOKEN not set!")


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
            logging.warning(
                "Plugin loading failed (non-fatal): %s",
                plugin_name,
                exc_info=True
            )

    print("------------------------------ DONE ------------------------------")


async def monitor_bot_sessions():
    """Background task to monitor bot sessions and auto-reconnect on expiry."""
    from .bot import multi_clients, work_loads
    from .bot.clients import initialize_clients, is_auth_key_error
    import sys
    
    while True:
        await asyncio.sleep(60)  # Check every minute
        
        try:
            # Check all Telethon clients
            for idx, client in list(multi_clients.items()):
                if not isinstance(idx, int):  # Skip Pyrogram client
                    continue
                
                if not client.is_connected():
                    logger.warning("Client %s disconnected, attempting reconnection...", idx)
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    
                    # Remove from work_loads
                    work_loads.pop(idx, None)
                    
                    # Re-initialize all clients
                    logger.info("Re-initializing all Telegram clients...")
                    await initialize_clients()
                    
                    # Verify bot is working
                    from .bot import StreamBot as UpdatedStreamBot
                    bot_info = await UpdatedStreamBot.get_me()
                    UpdatedStreamBot.username = bot_info.username
                    logger.info("Bot reconnected successfully: @%s", bot_info.username)
                    break  # Only need to reinitialize once
                    
        except Exception as e:
            logger.warning("Session monitor error: %s", e)
            await asyncio.sleep(30)


async def start_services():

    app_runner = None
    ping_task = None
    bot_init_task = None

    try:

        # Start web server FIRST (so health checks pass immediately)
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

        print(f"Server IP  =>> {bind_address}:{Var.PORT}")

        if Var.ON_HEROKU:
            print(f"App URL    =>> {Var.FQDN}")

        print("------------------------------------------------------------------")

        print(
            "Give a star: "
            "https://github.com/adarsh-goel/filestreambot-pro"
        )

        print("------------------------------------------------------------------")

        # Initialize Telegram clients in BACKGROUND with retries
        print("\n")
        print("------------------- Initializing Telegram Bot -------------------")

        async def init_bot_with_retry():
            max_retries = 10  # Increased for persistent Telegram issues
            base_delay = 30  # Start with 30s for Telegram internal issues
            attempt = 0
            
            while True:
                attempt += 1
                try:
                    logging.info("Starting main bot (attempt %s)", attempt)
                    
                    await initialize_clients()
                    
                    # Get updated StreamBot reference after initialization
                    from .bot import StreamBot as UpdatedStreamBot
                    
                    # Verify bot is working
                    bot_info = await UpdatedStreamBot.get_me()
                    UpdatedStreamBot.username = bot_info.username
                    logging.info("Bot health check passed: @%s", bot_info.username)
                    
                    # Start Pyrogram client for bot commands (plugins)
                    try:
                        from .bot import multi_clients
                        PyroBot = multi_clients.get("pyro")
                        if PyroBot:
                            await PyroBot.start()
                            logging.info("Pyrogram client started for bot commands")
                    except Exception as e:
                        logging.warning("Failed to start Pyrogram client: %s", e)
                    
                    # Initialize plugins after successful bot connection (non-blocking)
                    try:
                        await load_plugins()
                    except Exception as e:
                        logging.warning("Plugin loading failed (non-fatal): %s", e)
                    
                    print("------------------------------ DONE ------------------------------")
                    print()
                    print(f"Bot        =>> {bot_info.first_name}")
                    print(f"Owner      =>> {Var.OWNER_USERNAME}")
                    print("------------------------------------------------------------------")
                    
                    # Start background session monitor to auto-reconnect on session expiry
                    asyncio.create_task(monitor_bot_sessions())
                    return
                    
                except Exception as e:
                    logging.warning("Main bot startup failed (attempt %s): %s", attempt, e)
                    
                    try:
                        await UpdatedStreamBot.disconnect()
                    except Exception:
                        pass
                    
                    # Exponential backoff: 30s, 60s, 120s, 120s, 120s... (capped at 120s)
                    delay = min(base_delay * (2 ** (attempt - 1)), 120)
                    logging.info("Waiting %ss before retry (attempt %s)...", delay, attempt + 1)
                    await asyncio.sleep(delay)

        # Start bot initialization in background
        bot_init_task = asyncio.create_task(init_bot_with_retry())

        # Keep Alive
        if Var.ON_HEROKU:

            print("------------------ Starting Keep Alive Service ------------------")

            ping_task = asyncio.create_task(ping_server())

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

        if bot_init_task and not bot_init_task.done():
            bot_init_task.cancel()
            try:
                await bot_init_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logging.exception("Bot init shutdown error")

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
