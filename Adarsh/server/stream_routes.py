
import asyncio
import logging
import math
from urllib.parse import quote

from aiohttp import web

from Adarsh.bot import multi_clients, work_loads
from Adarsh.vars import Var
from Adarsh.server.exceptions import FIleNotFound
from Adarsh.utils.custom_dl import ByteStreamer


logger = logging.getLogger(__name__)

CHUNK_SIZE = 512 * 1024

# One streamer instance per Telegram client.
streamers = {}


def get_streamer(index):
    if index not in streamers:
        streamers[index] = ByteStreamer(multi_clients[index])

    return streamers[index]


def parse_range_header(range_header, file_size):
    """
    Parse a single HTTP byte range.

    Supports:
      bytes=0-1023
      bytes=1024-
      bytes=-1024
    """

    if not range_header:
        return 0, file_size - 1

    if not range_header.startswith("bytes="):
        raise ValueError("Invalid range unit")

    range_value = range_header.replace("bytes=", "", 1).strip()

    # Multiple ranges are not supported.
    if "," in range_value:
        raise ValueError("Multiple ranges are not supported")

    start_text, end_text = range_value.split("-", 1)

    if not start_text:
        # Suffix range: last N bytes.
        suffix_length = int(end_text)

        if suffix_length <= 0:
            raise ValueError("Invalid suffix range")

        start = max(file_size - suffix_length, 0)
        end = file_size - 1

    else:
        start = int(start_text)
        end = int(end_text) if end_text else file_size - 1

    if start < 0 or end < start or start >= file_size:
        raise ValueError("Requested range is not satisfiable")

    end = min(end, file_size - 1)

    return start, end


async def media_streamer(request):
    """
    Stream a Telegram file using Telethon.

    Expected route parameters:
        /watch/{id}/{file_name}
        /dl/{id}/{file_name}

    The message ID is the ID of the file in BIN_CHANNEL.
    """

    try:
        raw_file_id = (
            request.match_info.get("id")
            or request.match_info.get("file_id")
        )

        if not raw_file_id:
            return web.Response(
                text="Missing file ID",
                status=400
            )

        try:
            file_id = int(raw_file_id)
        except (TypeError, ValueError):
            return web.Response(
                text="Invalid file ID",
                status=400
            )

        if not multi_clients:
            return web.Response(
                text="Telegram clients are unavailable",
                status=503
            )

        # Select the client with the lowest current workload.
        index = min(
            multi_clients.keys(),
            key=lambda client_index: work_loads.get(client_index, 0)
        )

        streamer = get_streamer(index)

        # Retrieve Telegram file metadata.
        file_properties = await streamer.get_file_properties(file_id)

        file_size = int(file_properties.file_size)

        if file_size <= 0:
            return web.Response(
                text="Invalid file size",
                status=404
            )

        file_name = (
            request.match_info.get("file_name")
            or file_properties.file_name
            or f"file_{file_id}"
        )

        mime_type = (
            file_properties.mime_type
            or "application/octet-stream"
        )

        # Determine the requested byte range.
        range_header = request.headers.get("Range")

        try:
            start, end = parse_range_header(
                range_header,
                file_size
            )
        except (ValueError, TypeError):
            return web.Response(
                status=416,
                headers={
                    "Content-Range": f"bytes */{file_size}",
                    "Accept-Ranges": "bytes"
                }
            )

        content_length = end - start + 1

        # Calculate Telegram chunk boundaries.
        offset = (start // CHUNK_SIZE) * CHUNK_SIZE

        first_part_cut = start - offset

        last_part_cut = (end % CHUNK_SIZE) + 1

        part_count = (
            (end // CHUNK_SIZE)
            - (start // CHUNK_SIZE)
            + 1
        )

        status = 206 if range_header else 200

        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(content_length),
            "Accept-Ranges": "bytes",
            "Content-Disposition": (
                f'inline; filename="{quote(file_name)}"'
            ),
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff"
        }

        if status == 206:
            headers["Content-Range"] = (
                f"bytes {start}-{end}/{file_size}"
            )

        response = web.StreamResponse(
            status=status,
            headers=headers
        )

        await response.prepare(request)

        # Ask ByteStreamer for the requested Telegram chunks.
        body = streamer.yield_file(
            file_properties=file_properties,
            index=index,
            offset=offset,
            first_part_cut=first_part_cut,
            last_part_cut=last_part_cut,
            part_count=part_count,
            chunk_size=CHUNK_SIZE
        )

        bytes_sent = 0

        try:
            async for chunk in body:
                if not chunk:
                    continue

                # Do not send more bytes than the HTTP range requested.
                remaining = content_length - bytes_sent

                if remaining <= 0:
                    break

                if len(chunk) > remaining:
                    chunk = chunk[:remaining]

                await response.write(chunk)

                bytes_sent += len(chunk)

            if bytes_sent != content_length:
                logger.warning(
                    "Incomplete stream for file %s: sent %s of %s bytes",
                    file_id,
                    bytes_sent,
                    content_length
                )

        except (ConnectionResetError, asyncio.CancelledError):
            logger.info(
                "Client disconnected while streaming file %s",
                file_id
            )
            raise

        finally:
            await body.aclose()

        await response.write_eof()

        return response

    except FIleNotFound:
        return web.Response(
            text="File not found",
            status=404
        )

    except asyncio.CancelledError:
        raise

    except Exception:
        logger.exception("File streaming failed")

        if not request.transport or request.transport.is_closing():
            return web.Response(status=500)

        return web.Response(
            text="Internal streaming error",
            status=500
        )
