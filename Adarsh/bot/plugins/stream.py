
# (c) Adarsh-Goel

import os
import asyncio
import logging

from urllib.parse import quote_plus, urlparse

from Adarsh.bot import multi_clients
from Adarsh.utils.database import Database
from Adarsh.utils.human_readable import humanbytes
from Adarsh.vars import Var

from pyrogram import filters, Client
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from Adarsh.utils.file_properties import (
    get_name,
    get_hash,
    get_media_file_size
)


# Database initialization
db = Database(Var.DATABASE_URL, Var.name)
pass_db = Database(Var.DATABASE_URL, "ag_passwords")

MY_PASS = os.environ.get("MY_PASS")

# Retained for compatibility
pass_dict = {}

# Use Pyrogram client for bot commands
PyroBot = multi_clients.get("pyro")


# --------------------------------------------------
# URL VALIDATION
# --------------------------------------------------

def make_url(path):
    base = Var.URL.strip()
    parsed = urlparse(base)

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError(f"Invalid BASE URL: {base}")

    if parsed.port and not (1 <= parsed.port <= 65535):
        raise ValueError(f"Invalid URL port: {base}")

    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def generate_links(log_msg):
    file_name = quote_plus(get_name(log_msg))
    file_hash = get_hash(log_msg)

    stream_link = make_url(
        f"watch/{log_msg.id}/{file_name}?hash={file_hash}"
    )

    online_link = make_url(
        f"{log_msg.id}/{file_name}?hash={file_hash}"
    )

    return stream_link, online_link


# --------------------------------------------------
# LOGIN HANDLER
# --------------------------------------------------

if PyroBot:
    @PyroBot.on_message(
        (filters.regex("login🔑") | filters.command("login")),
        group=4
    )
    async def login_handler(c: Client, m: Message):

        if not MY_PASS:
            await m.reply_text("Password login is disabled.")
            return

        try:
            ag = await m.reply_text(
                "Now send me password.\n\n"
                "If you don't know, check the MY_PASS variable "
                "in your hosting environment.\n\n"
                "Send /cancel to cancel."
            )

            try:
                text_msg = await c.listen(
                    m.chat.id,
                    filters=filters.text,
                    timeout=90
                )

            except asyncio.TimeoutError:
                await ag.edit(
                    "I can't wait more for password. Please try again."
                )
                return

            if not text_msg or not text_msg.text:
                await ag.edit("Invalid password input.")
                return

            textp = text_msg.text.strip()

            if textp == "/cancel":
                await ag.edit("Process Cancelled Successfully.")
                return

            if textp == MY_PASS:
                await pass_db.add_user_pass(m.chat.id, textp)

                await ag.edit(
                    "Yeah! You entered the password correctly."
                )

            else:
                await ag.edit("Wrong password. Please try again.")

        except Exception:
            logging.exception("Login handler error")
            try:
                await m.reply_text("An error occurred during login.")
            except Exception:
                pass


# --------------------------------------------------
# PRIVATE FILE HANDLER
# --------------------------------------------------

if PyroBot:
    @PyroBot.on_message(
        filters.private
        & (filters.document | filters.video | filters.audio | filters.photo),
        group=4
    )
    async def private_receive_handler(c: Client, m: Message):

        if not m.from_user:
            return

        # Password verification
        if MY_PASS:

            check_pass = await pass_db.get_user_pass(m.chat.id)

            if check_pass is None:
                await m.reply_text(
                    "Login first using /login command.\n"
                    "Don't know the password? Request it from the developer."
                )
                return

            if check_pass != MY_PASS:
                await pass_db.delete_user(m.chat.id)
                await m.reply_text("Your login session is invalid. Please login again.")
                return

        # Register user
        try:
            if not await db.is_user_exist(m.from_user.id):

                await db.add_user(m.from_user.id)

                await c.send_message(
                    Var.BIN_CHANNEL,
                    f"New User Joined!\n\n"
                    f"Name: [{m.from_user.first_name}]"
                    f"(tg://user?id={m.from_user.id})\n"
                    f"Started Your Bot!"
                )

        except Exception:
            logging.exception("User registration error")

        # Updates channel verification
        if Var.UPDATES_CHANNEL != "None":

            try:
                user = await c.get_chat_member(
                    Var.UPDATES_CHANNEL,
                    m.from_user.id
                )

                if user.status == "kicked":

                    await c.send_message(
                        chat_id=m.chat.id,
                        text=(
                            "You are banned!\n\n"
                            "**Contact Developer "
                            "[Adarsh Goel](https://github.com/adarsh-goel) "
                            "for help.**"
                        ),
                        disable_web_page_preview=True
                    )
                    return

            except UserNotParticipant:

                await c.send_message(
                    chat_id=m.chat.id,
                    text="<i>JOIN UPDATES CHANNEL TO USE ME 🔐</i>",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "Join Now 🔓",
                                    url=f"https://t.me/{Var.UPDATES_CHANNEL.lstrip('@')}"
                                )
                            ]
                        ]
                    )
                )
                return

            except Exception:
                logging.exception("Membership verification error")

                await c.send_message(
                    chat_id=m.chat.id,
                    text=(
                        "Something went wrong while checking "
                        "channel membership. Please try again later."
                    )
                )
                return

        # Forward and generate links
        try:

            log_msg = await m.forward(
                chat_id=Var.BIN_CHANNEL
            )

            stream_link, online_link = generate_links(log_msg)

            msg_text = (
                "<i><u>𝗬𝗼𝘂𝗿 𝗟𝗶𝗻𝗸 𝗚𝗲𝗻𝗲𝗿𝗮𝘁𝗲𝗱 !</u></i>\n\n"
                "<b>📂 Fɪʟᴇ Nᴀᴍᴇ :</b> <i>{}</i>\n\n"
                "<b>📦 Fɪʟᴇ Sɪᴢᴇ :</b> <i>{}</i>\n\n"
                "<b>📥 Dᴏᴡɴʟᴏᴀᴅ :</b> <i>{}</i>\n\n"
                "<b>🖥 WATCH :</b> <i>{}</i>\n\n"
                "<b>🚸 Nᴏᴛᴇ : LINK WON'T EXPIRE TILL I DELETE</b>"
            )

            await log_msg.reply_text(
                text=(
                    f"**Requested By:** "
                    f"[{m.from_user.first_name}]"
                    f"(tg://user?id={m.from_user.id})\n"
                    f"**User ID:** `{m.from_user.id}`\n"
                    f"**Stream Link:** {stream_link}"
                ),
                disable_web_page_preview=True,
                quote=True
            )

            await m.reply_text(
                text=msg_text.format(
                    get_name(log_msg),
                    humanbytes(get_media_file_size(log_msg)),
                    online_link,
                    stream_link
                ),
            quote=True,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "STREAM 🖥",
                            url=stream_link
                        ),
                        InlineKeyboardButton(
                            "DOWNLOAD 📥",
                            url=online_link
                        )
                    ]
                ]
            )
        )

        except FloodWait as e:

            logging.warning(f"FloodWait: sleeping for {e.value} seconds")

            await asyncio.sleep(e.value)

            await c.send_message(
                chat_id=Var.BIN_CHANNEL,
                text=(
                    f"Got FloodWait of {e.value}s from "
                    f"[{m.from_user.first_name}]"
                    f"(tg://user?id={m.from_user.id})\n\n"
                    f"User ID: `{m.from_user.id}`"
                ),
                disable_web_page_preview=True
            )

        except Exception:
            logging.exception("Private file processing error")

            try:
                await m.reply_text(
                    "An error occurred while processing your file. Please try again."
                )
            except Exception:
                pass


# --------------------------------------------------
# CHANNEL FILE HANDLER
# --------------------------------------------------

if PyroBot:
    @PyroBot.on_message(
        filters.channel
        & ~filters.group
        & (filters.document | filters.video | filters.photo)
        & ~filters.forwarded,
        group=-1
    )
    async def channel_receive_handler(bot: Client, broadcast: Message):

        # Password verification
        if MY_PASS:

            check_pass = await pass_db.get_user_pass(
                broadcast.chat.id
            )

            if check_pass is None:

                await broadcast.reply_text(
                    "Login first using /login command.\n"
                    "Don't know the password? Request it from the developer!"
                )
                return

            if check_pass != MY_PASS:

                await broadcast.reply_text(
                    "Wrong password. Login again."
                )

                await pass_db.delete_user(
                    broadcast.chat.id
                )
                return

        # Banned channel check
        if int(broadcast.chat.id) in Var.BANNED_CHANNELS:

            await bot.leave_chat(
                broadcast.chat.id
            )
            return

        try:

            # Forward file to BIN_CHANNEL
            log_msg = await broadcast.forward(
                chat_id=Var.BIN_CHANNEL
            )

            stream_link, online_link = generate_links(log_msg)

            await log_msg.reply_text(
                text=(
                    f"**Channel Name:** `{broadcast.chat.title}`\n"
                    f"**CHANNEL ID:** `{broadcast.chat.id}`\n"
                    f"**REQUEST URL:** {stream_link}"
                ),
                quote=True
            )

            # Add buttons to original channel message
            await bot.edit_message_reply_markup(
                chat_id=broadcast.chat.id,
                message_id=broadcast.id,
                reply_markup=InlineKeyboardMarkup(
                    [
                    [
                        InlineKeyboardButton(
                            "🖥 STREAM",
                            url=stream_link
                        ),
                        InlineKeyboardButton(
                            "DOWNLOAD 📥",
                            url=online_link
                        )
                    ]
                ]
            )
        )

        except FloodWait as e:

            logging.warning(
                f"Channel FloodWait: sleeping for {e.value} seconds"
            )

            await asyncio.sleep(e.value)

            await bot.send_message(
                chat_id=Var.BIN_CHANNEL,
                text=(
                    f"Got FloodWait of {e.value}s "
                    f"from {broadcast.chat.title}\n\n"
                    f"CHANNEL ID: `{broadcast.chat.id}`"
                ),
                disable_web_page_preview=True
            )

        except Exception:

            logging.exception(
                "Channel file processing error"
            )

            try:
                await bot.send_message(
                    chat_id=Var.BIN_CHANNEL,
                    text=(
                        f"Channel processing error.\n"
                        f"Channel: {broadcast.chat.title}\n"
                        f"Channel ID: `{broadcast.chat.id}`\n"
                        f"Please check application logs."
                    )
                )
            except Exception:
                pass
