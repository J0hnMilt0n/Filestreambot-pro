
# (c) adarsh-goel

import os
import logging
from os import getenv, environ
from dotenv import load_dotenv

load_dotenv()


def get_bool(name, default=False):
    value = getenv(name, str(default))
    return value.strip().lower() in ("true", "1", "yes", "on")


class Var(object):

    MULTI_CLIENT = False

    API_ID = int(getenv("API_ID"))
    API_HASH = getenv("API_HASH")
    BOT_TOKEN = getenv("BOT_TOKEN")

    name = getenv("name", "file2linkbot")

    SLEEP_THRESHOLD = int(getenv("SLEEP_THRESHOLD", "60"))
    WORKERS = int(getenv("WORKERS", "4"))

    # MTProto Proxy Configuration
    TELEGRAM_PROXY_HOST = getenv(
        "TELEGRAM_PROXY_HOST", "tg.prxtg.top"
    )

    TELEGRAM_PROXY_PORT = int(
        getenv("TELEGRAM_PROXY_PORT", "443")
    )

    TELEGRAM_PROXY_SECRET = getenv(
        "TELEGRAM_PROXY_SECRET", ""
    )

    BIN_CHANNEL = int(getenv("BIN_CHANNEL"))
    PORT = int(getenv("PORT", "8080"))

    BIND_ADRESS = getenv("WEB_SERVER_BIND_ADDRESS", "0.0.0.0")

    PING_INTERVAL = int(getenv("PING_INTERVAL", "1200"))

    OWNER_ID = {
        int(x) for x in getenv("OWNER_ID", "").split()
    }

    NO_PORT = get_bool("NO_PORT", False)

    OWNER_USERNAME = getenv("OWNER_USERNAME", "")

    ON_HEROKU = "DYNO" in environ
    ON_KOYEB = "KOYEB_APP_NAME" in environ or "KOYEB_SERVICE_NAME" in environ

    APP_NAME = getenv("APP_NAME", "") if ON_HEROKU else None
    KOYEB_APP_NAME = getenv("KOYEB_APP_NAME", "") if ON_KOYEB else None

    HAS_SSL = get_bool("HAS_SSL", True)

    FQDN = getenv("FQDN", "").strip()

    if not FQDN and ON_HEROKU and APP_NAME:
        FQDN = f"{APP_NAME}.herokuapp.com"

    if not FQDN and ON_KOYEB and KOYEB_APP_NAME:
        FQDN = f"{KOYEB_APP_NAME}.koyeb.app"

    if not FQDN:
        # Default to localhost for local dev, but warn
        FQDN = "localhost"
        logging.warning("FQDN not set, defaulting to localhost. Set FQDN env var for production.")

    FQDN = (
        FQDN.replace("https://", "")
            .replace("http://", "")
            .strip("/")
    )

    PROTOCOL = "https" if HAS_SSL else "http"

    URL = f"{PROTOCOL}://{FQDN}/"

    DATABASE_URL = getenv("DATABASE_URL")

    UPDATES_CHANNEL = getenv("UPDATES_CHANNEL", "None")

    BANNED_CHANNELS = list(
        set(
            int(x)
            for x in getenv(
                "BANNED_CHANNELS",
                "-1001362659779"
            ).split()
        )
    )
