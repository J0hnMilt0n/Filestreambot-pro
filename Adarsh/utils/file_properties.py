
# (c) adarsh-goel

import hashlib
import logging

from dataclasses import dataclass
from typing import Any, Optional

from telethon import TelegramClient
from telethon.tl.types import Document, Photo

from Adarsh.server.exceptions import FIleNotFound


@dataclass
class FileProperties:
    message: Any
    media: Any
    file_size: int
    mime_type: str
    file_name: str
    unique_id: str

    @property
    def dc_id(self):
        return getattr(self.media, "dc_id", None)

    @property
    def media_id(self):
        return getattr(self.media, "id", None)


def get_media_from_message(message):
    if not message:
        return None

    document = getattr(message, "document", None)

    if isinstance(document, Document):
        return document

    photo = getattr(message, "photo", None)

    if isinstance(photo, Photo):
        return photo

    return None


def generate_unique_id(message, media=None):
    media = media or get_media_from_message(message)

    if not media:
        return ""

    identity = (
        f"{message.chat_id}:"
        f"{message.id}:"
        f"{type(media).__name__}:"
        f"{media.id}"
    )

    return hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()


async def parse_file_id(message):
    return get_media_from_message(message)


async def parse_file_unique_id(message):
    return generate_unique_id(message)


async def get_file_ids(
    client: TelegramClient,
    chat_id: int,
    id: int
) -> Optional[FileProperties]:

    try:
        message = await client.get_messages(
            chat_id,
            ids=id
        )

        if not message:
            raise FIleNotFound

        media = get_media_from_message(message)

        if not media:
            raise FIleNotFound

        file_info = message.file

        file_size = (
            getattr(file_info, "size", None)
            or getattr(media, "size", 0)
            or 0
        )

        mime_type = (
            getattr(file_info, "mime_type", None)
            or getattr(media, "mime_type", None)
            or ""
        )

        file_name = (
            getattr(file_info, "name", None)
            or ""
        )

        unique_id = generate_unique_id(
            message,
            media
        )

        if not file_size:
            logging.warning(
                "File size unavailable for message %s",
                id
            )

        return FileProperties(
            message=message,
            media=media,
            file_size=int(file_size),
            mime_type=mime_type,
            file_name=file_name,
            unique_id=unique_id
        )

    except FIleNotFound:
        raise

    except Exception:
        logging.exception(
            "Unable to retrieve file properties for message %s",
            id
        )
        raise FIleNotFound


def get_hash(media_msg) -> str:
    return generate_unique_id(media_msg)[:6]


def get_name(media_msg) -> str:
    if not get_media_from_message(media_msg):
        return "Unknown"

    return (
        getattr(media_msg.file, "name", None)
        or "Unknown"
    )


def get_media_file_size(message) -> int:
    if not get_media_from_message(message):
        return 0

    return int(
        getattr(message.file, "size", 0) or 0
    )
