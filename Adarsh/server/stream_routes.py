
import re
import time
import math
import logging
import mimetypes
import secrets
from urllib.parse import quote

from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from pyrogram.errors import FloodWait, RPCError

from Adarsh.bot import multi_clients, work_loads
from Adarsh.server.exceptions import FIleNotFound, InvalidHash
from Adarsh import StartTime
from Adarsh.utils.time_format import get_readable_time
from Adarsh.utils.custom_dl import ByteStreamer
from Adarsh.utils.render_template import render_page
from Adarsh.vars import Var


routes = web.RouteTableDef()
class_cache = {}


@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.json_response({
        "server_status": "running",
        "uptime": get_readable_time(time.time() - StartTime)
    })


def parse_file_path(path, query):
    """
    Extract Telegram message ID and secure hash.
    """
    match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)

    if match:
        secure_hash = match.group(1)
        file_id = int(match.group(2))
    else:
        match = re.search(r"(\d+)", path)

        if not match:
            raise web.HTTPNotFound(text="Invalid file URL")

        file_id = int(match.group(1))
        secure_hash = query.get("hash")

    return file_id, secure_hash


@routes.get(r"/watch/{path:\S+}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        path = request.match_info["path"]

        file_id, secure_hash = parse_file_path(
            path,
            request.rel_url.query
        )

        html = await render_page(file_id, secure_hash)

        return web.Response(
            text=html,
            content_type="text/html"
        )

    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)

    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)

    except (AttributeError, BadStatusLine, ConnectionResetError):
        raise web.HTTPBadRequest(text="Invalid request")

    except web.HTTPException:
        raise

    except Exception:
        logging.exception("Watch handler error")
        raise web.HTTPInternalServerError(
            text="Unable to load video page"
        )


@routes.get(r"/{path:\S+}", allow_head=True)
async def download_handler(request: web.Request):
    try:
        path = request.match_info["path"]

        file_id, secure_hash = parse_file_path(
            path,
            request.rel_url.query
        )

        return await media_streamer(
            request,
            file_id,
            secure_hash
        )

    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)

    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)

    except (AttributeError, BadStatusLine, ConnectionResetError):
        raise web.HTTPBadRequest(text="Invalid request")

    except web.HTTPException:
        raise

    except (FloodWait, RPCError):
        logging.exception("Telegram API error")
        raise web.HTTPServiceUnavailable(
            text="Telegram is temporarily unavailable. Please retry."
        )

    except Exception:
        logging.exception("Download handler error")
        raise web.HTTPInternalServerError(
            text="Unable to stream file"
        )


async def get_streamer(client):
    if client not in class_cache:
        class_cache[client] = ByteStreamer(client)

    return class_cache[client]


def parse_range_header(range_header, file_size):
    """
    Parse HTTP Range headers.

    Supports:
    bytes=100-200
    bytes=100-
    bytes=-500
    """

    if not range_header:
        return 0, file_size - 1

    match = re.fullmatch(
        r"bytes=(\d*)-(\d*)",
        range_header.strip()
    )

    if not match:
        raise ValueError("Invalid range")

    start_str, end_str = match.groups()

    if not start_str and not end_str:
        raise ValueError("Invalid range")

    if not start_str:
        suffix_length = int(end_str)

        if suffix_length <= 0:
            raise ValueError("Invalid suffix")

        start = max(file_size - suffix_length, 0)
        end = file_size - 1

    else:
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1

    if start >= file_size or end < start:
        raise ValueError("Range not satisfiable")

    end = min(end, file_size - 1)

    return start, end


def safe_filename(filename):
    """
    Prevent unsafe characters in Content-Disposition.
    """
    filename = str(filename or "file.unknown")

    filename = filename.replace("\\", "_")
    filename = filename.replace('"', "_")
    filename = filename.replace("\r", "_")
    filename = filename.replace("\n", "_")

    return filename


async def media_streamer(
    request: web.Request,
    file_id: int,
    secure_hash: str
):
    if not multi_clients:
        raise web.HTTPServiceUnavailable(
            text="No Telegram clients available"
        )

    if not work_loads:
        raise web.HTTPServiceUnavailable(
            text="Streaming workers unavailable"
        )

    index = min(
        range(len(work_loads)),
        key=lambda i: work_loads[i]
    )

    faster_client = multi_clients[index]

    if Var.MULTI_CLIENT:
        logging.info(
            "Client %s serving %s",
            index,
            request.remote
        )

    tg_connect = await get_streamer(faster_client)

    # Get Telegram file properties
    file_properties = await tg_connect.get_file_properties(file_id)

    if (
        not secure_hash
        or file_properties.unique_id[:6] != secure_hash
    ):
        raise InvalidHash

    file_size = file_properties.file_size

    if file_size <= 0:
        raise web.HTTPNotFound(text="Empty file")

    range_header = request.headers.get("Range")

    # Parse HTTP range
    try:
        from_bytes, until_bytes = parse_range_header(
            range_header,
            file_size
        )

    except ValueError:
        return web.Response(
            status=416,
            text="416: Range not satisfiable",
            headers={
                "Content-Range": f"bytes */{file_size}",
                "Accept-Ranges": "bytes"
            }
        )

    # Calculate chunks
    chunk_size = 1024 * 1024

    offset = (
        from_bytes // chunk_size
    ) * chunk_size

    first_part_cut = from_bytes - offset

    last_part_cut = (
        until_bytes % chunk_size
    ) + 1

    req_length = (
        until_bytes - from_bytes + 1
    )

    part_count = math.ceil(
        (first_part_cut + req_length) / chunk_size
    )

    # Telegram file generator
    body = tg_connect.yield_file(
        file_properties,
        index,
        offset,
        first_part_cut,
        last_part_cut,
        part_count,
        chunk_size
    )

    # MIME detection
    file_name = safe_filename(
        file_properties.file_name
        or f"{secrets.token_hex(4)}.unknown"
    )

    mime_type = file_properties.mime_type

    if not mime_type:
        mime_type = mimetypes.guess_type(file_name)[0]

    if not mime_type:
        mime_type = "application/octet-stream"

    # Response headers
    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(req_length),
        "Content-Disposition": (
            f"attachment; filename*=UTF-8''{quote(file_name)}"
        ),
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache"
    }

    if range_header:
        headers["Content-Range"] = (
            f"bytes {from_bytes}-{until_bytes}/{file_size}"
        )

    return web.Response(
        status=206 if range_header else 200,
        body=body,
        headers=headers
    )
