# (c) adarsh-goel

import asyncio
import logging
import os
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
        # Cookie file for YouTube authentication (optional)
        self.cookie_file = os.getenv("YOUTUBE_COOKIES_FILE", "/tmp/youtube_cookies.txt")
        # Cookie content from env var (alternative to file)
        self.cookie_content = os.getenv("YOUTUBE_COOKIES")
        self._cookie_temp_file = None

    def _prepare_cookies(self) -> Optional[str]:
        """Prepare cookie file from env var or existing file. Returns path or None."""
        # Priority 1: Cookie content from env var (write to temp file)
        if self.cookie_content:
            import tempfile
            self._cookie_temp_file = tempfile.NamedTemporaryFile(
                mode='w', suffix='.txt', delete=False, prefix='yt_cookies_'
            )
            self._cookie_temp_file.write(self.cookie_content)
            self._cookie_temp_file.close()
            logger.info("Using YouTube cookies from YOUTUBE_COOKIES env var")
            return self._cookie_temp_file.name
        
        # Priority 2: Cookie file from env var or default
        if os.path.exists(self.cookie_file):
            logger.info("Using YouTube cookies from %s", self.cookie_file)
            return self.cookie_file
        
        return None

    def _cleanup_cookies(self):
        """Clean up temporary cookie file."""
        if self._cookie_temp_file and os.path.exists(self._cookie_temp_file.name):
            try:
                os.unlink(self._cookie_temp_file.name)
            except Exception:
                pass
            self._cookie_temp_file = None

    def _get_base_cmd(self):
        """Base yt-dlp command with anti-bot options."""
        cmd = [
            self.yt_dlp_path,
            "--no-check-certificates",
            "--no-warnings",
            "--ignore-errors",
            "--extractor-retries", "3",
            "--socket-timeout", "30",
            "--extractor-args", "youtube:player_client=android_embedded",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "--referer", "https://www.youtube.com/",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
        ]
        
        # Add cookies from env var or file
        cookie_path = self._prepare_cookies()
        if cookie_path:
            cmd.extend(["--cookies", cookie_path])
        
        return cmd

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
        cmd = self._get_base_cmd() + [
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
        """Get direct stream URL for a YouTube video with fallback formats."""
        # Try multiple format selectors in order of preference
        formats = [
            quality,
            "best[ext=mp4]/best",
            "best[protocol^=http]/best",
            "worst[ext=mp4]/worst",
            "best",
        ]
        
        last_error = None
        for fmt in formats:
            cmd = self._get_base_cmd() + [
                "-g",
                "-f", fmt,
                url
            ]
            
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                
                if proc.returncode == 0:
                    urls = stdout.decode().strip().split('\n')
                    if urls and urls[0]:
                        logger.info("Got stream URL with format: %s", fmt)
                        self._cleanup_cookies()
                        return urls[0]
                
                last_error = stderr.decode()
                logger.warning("Format %s failed: %s", fmt, last_error[:200])
                
            except Exception as e:
                last_error = str(e)
                logger.warning("Format %s exception: %s", fmt, e)
        
        self._cleanup_cookies()
        raise Exception(f"All format attempts failed. Last error: {last_error}")

    async def stream_video(
        self,
        url: str,
        start: int = 0,
        end: Optional[int] = None,
        chunk_size: int = 512 * 1024,
        quality: str = "best[ext=mp4]/best"
    ) -> AsyncGenerator[bytes, None]:
        """Stream video content by proxying direct URL from yt-dlp."""
        
        try:
            # Get direct stream URL
            stream_url = await self.get_stream_url(url, quality)
            if not stream_url:
                raise Exception("No stream URL found")
            
            logger.info("Streaming YouTube video via direct URL: %s...", stream_url[:50])
            
            # Use aiohttp to proxy the stream with same headers/cookies as yt-dlp
            import aiohttp
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.youtube.com/",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://www.youtube.com",
            }
            if start or end:
                range_val = f"bytes={start}-"
                if end:
                    range_val += str(end)
                headers["Range"] = range_val
            
            # Prepare cookies for the streaming request
            cookie_path = self._prepare_cookies()
            cookie_jar = None
            if cookie_path:
                import http.cookiejar
                cookie_jar = http.cookiejar.MozillaCookieJar(cookie_path)
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
            
            timeout = aiohttp.ClientTimeout(total=300, connect=30)
            
            connector = aiohttp.TCPConnector(limit=10)
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                cookie_jar=cookie_jar
            ) as session:
                async with session.get(stream_url, headers=headers) as resp:
                    if resp.status not in (200, 206):
                        # Log response headers for debugging
                        logger.error("Stream request failed: %s, headers: %s", resp.status, dict(resp.headers))
                        raise Exception(f"Stream request failed: {resp.status}")
                    
                    async for chunk in resp.content.iter_chunked(chunk_size):
                        if not chunk:
                            continue
                        yield chunk
        finally:
            self._cleanup_cookies()


async def get_youtube_streamer() -> YouTubeStreamer:
    """Get or create YouTube streamer instance."""
    return YouTubeStreamer()