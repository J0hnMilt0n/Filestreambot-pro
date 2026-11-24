# (c) adarsh-goel

import asyncio
import logging
import shlex
from typing import Optional, AsyncGenerator

import aiohttp

logger = logging.getLogger(__name__)


class YouTubeStreamer:
    """Stream YouTube videos using yt-dlp."""

    def __init__(self):
        self.yt_dlp_path = "yt-dlp"
        self._ffmpeg_checked = False
        self._has_ffmpeg = False

    async def _check_ffmpeg(self) -> bool:
        """Check if ffmpeg is available."""
        if self._ffmpeg_checked:
            return self._has_ffmpeg
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            self._has_ffmpeg = proc.returncode == 0
        except Exception:
            self._has_ffmpeg = False
        
        self._ffmpeg_checked = True
        logger.info("ffmpeg available: %s", self._has_ffmpeg)
        return self._has_ffmpeg

    async def get_video_info(self, url: str) -> dict:
        """Get video metadata without downloading."""
        cmd = [
            self.yt_dlp_path,
            "--dump-json",
            "--no-download",
            "--skip-download",
            url
        ]
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                logger.error("yt-dlp info failed: %s", stderr.decode())
                raise Exception(f"Failed to get video info: {stderr.decode()}")
            
            import json
            return json.loads(stdout.decode())
        except Exception as e:
            logger.exception("Error getting video info")
            raise

    async def get_stream_url(self, url: str, quality: str = "best[ext=mp4]/best") -> str:
        """Get direct stream URL for a YouTube video."""
        cmd = [
            self.yt_dlp_path,
            "-g",
            "-f", quality,
            url
        ]
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                logger.error("yt-dlp get URL failed: %s", stderr.decode())
                raise Exception(f"Failed to get stream URL: {stderr.decode()}")
            
            urls = stdout.decode().strip().split('\n')
            return urls[0] if urls else None
        except Exception as e:
            logger.exception("Error getting stream URL")
            raise

    async def stream_video(
        self,
        url: str,
        start: int = 0,
        end: Optional[int] = None,
        chunk_size: int = 512 * 1024,
        quality: str = "best[ext=mp4]/best"
    ) -> AsyncGenerator[bytes, None]:
        """Stream video content by proxying direct URL from yt-dlp."""
        
        # Get direct stream URL
        stream_url = await self.get_stream_url(url, quality)
        if not stream_url:
            raise Exception("No stream URL found")
        
        logger.info("Streaming YouTube video via direct URL: %s...", stream_url[:50])
        
        # Use aiohttp to proxy the stream
        import aiohttp
        
        headers = {}
        if start or end:
            range_val = f"bytes={start}-"
            if end:
                range_val += str(end)
            headers["Range"] = range_val
        
        timeout = aiohttp.ClientTimeout(total=300, connect=30)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(stream_url, headers=headers) as resp:
                if resp.status not in (200, 206):
                    raise Exception(f"Stream request failed: {resp.status}")
                
                async for chunk in resp.content.iter_chunked(chunk_size):
                    if not chunk:
                        continue
                    yield chunk


async def get_youtube_streamer() -> YouTubeStreamer:
    """Get or create YouTube streamer instance."""
    return YouTubeStreamer()