# (c) adarsh-goel

import asyncio
import base64
import logging
import os
import shlex
from typing import Optional, AsyncGenerator, Dict, Any

import aiohttp

logger = logging.getLogger(__name__)


class YouTubeStreamer:
    """Stream YouTube videos using yt-dlp with Invidious fallback."""

    def __init__(self):
        self.yt_dlp_path = "yt-dlp"
        self._ffmpeg_checked = False
        self._has_ffmpeg = False
        self._deno_checked = False
        self._has_deno = False
        # Cookie file for YouTube authentication (optional)
        self.cookie_file = os.getenv("YOUTUBE_COOKIES_FILE", "/tmp/youtube_cookies.txt")
        # Cookie content from env var (alternative to file)
        self.cookie_content = os.getenv("YOUTUBE_COOKIES")
        self._cookie_temp_file = None
        # Cookie invalidation state (persists across method calls)
        self._cookies_invalidated = False
        # Cache for video info to avoid duplicate calls
        self._info_cache: Dict[str, Dict[str, Any]] = {}

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
        
        # Priority 2: Base64 encoded cookies from env var (write to temp file)
        b64_cookies = os.getenv("YOUTUBE_COOKIES_B64")
        if b64_cookies:
            import tempfile
            try:
                decoded = base64.b64decode(b64_cookies).decode()
                self._cookie_temp_file = tempfile.NamedTemporaryFile(
                    mode='w', suffix='.txt', delete=False, prefix='yt_cookies_'
                )
                self._cookie_temp_file.write(decoded)
                self._cookie_temp_file.close()
                logger.info("Using YouTube cookies from YOUTUBE_COOKIES_B64 env var")
                return self._cookie_temp_file.name
            except Exception as e:
                logger.warning("Failed to decode YOUTUBE_COOKIES_B64: %s", e)
        
        # Priority 3: Cookie file from env var or default (only if not invalidated)
        if self.cookie_file and os.path.exists(self.cookie_file):
            # Validate cookie file is readable, has content, AND is parseable by MozillaCookieJar
            try:
                with open(self.cookie_file, 'r') as f:
                    content = f.read().strip()
                if not content:
                    logger.warning("Cookie file %s is empty, ignoring", self.cookie_file)
                    return None
                
                # Validate cookie format by trying to parse with MozillaCookieJar
                import http.cookiejar
                test_jar = http.cookiejar.MozillaCookieJar()
                test_jar.load(self.cookie_file, ignore_discard=True, ignore_expires=True)
                if len(test_jar) == 0:
                    logger.warning("Cookie file %s has no valid cookies (parsing failed or all entries invalid), ignoring", self.cookie_file)
                    return None
                
                logger.info("Using YouTube cookies from %s (%d cookies)", self.cookie_file, len(test_jar))
                return self.cookie_file
            except http.cookiejar.LoadError as e:
                logger.warning("Cookie file %s format invalid (LoadError: %s), ignoring", self.cookie_file, e)
                return None
            except Exception as e:
                logger.warning("Cannot read/parse cookie file %s: %s", self.cookie_file, e)
                return None
        
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
        """Base yt-dlp command with anti-bot options and SABR support."""
        # Ensure Deno is in PATH for n challenge solving (YouTube SABR experiment)
        import os
        env = os.environ.copy()
        deno_path = "/root/.deno/bin"
        if deno_path not in env.get("PATH", ""):
            env["PATH"] = deno_path + ":" + env.get("PATH", "")
        # Also add common Deno locations
        for p in ["/usr/local/bin", "/usr/bin", "/opt/deno"]:
            if p not in env.get("PATH", ""):
                env["PATH"] = p + ":" + env.get("PATH", "")
        
        cmd = [
            self.yt_dlp_path,
            "--no-check-certificates",
            "--no-warnings",
            "--ignore-errors",
            "--extractor-retries", "3",
            "--socket-timeout", "30",
            # Use player clients that support SABR (Segmented Adaptive Bitrate)
            # tv_embedded and android_embedded are known to work with SABR
            # This is the DEFAULT - individual methods can override
            "--extractor-args", "youtube:player_client=tv_embedded,android_embedded",
            # Age-gate bypass
            "--age-limit", "99",
            # NOTE: Removed --extractor-args youtube:skip=hls,dash
            # HLS/DASH are often the ONLY available formats. Skipping them causes
            # "Requested format is not available" for many videos.
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "--referer", "https://www.youtube.com/",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
        ]
        
        # Add cookies from env var or file
        cookie_path = self._prepare_cookies()
        if cookie_path:
            cmd.extend(["--cookies", cookie_path])
        
        return cmd, env

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

    async def _check_deno(self) -> bool:
        """Check if Deno is available for n-challenge solving (SABR experiment)."""
        if hasattr(self, '_deno_checked') and self._deno_checked:
            return getattr(self, '_has_deno', False)
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "deno", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            self._has_deno = proc.returncode == 0
            if self._has_deno:
                logger.info("Deno available: %s", stdout.decode().strip().split('\n')[0])
            else:
                logger.warning("Deno not available - SABR formats (tv_embedded, android_embedded) may fail")
        except Exception as e:
            self._has_deno = False
            logger.warning("Deno check failed: %s - SABR formats may fail", e)
        
        self._deno_checked = True
        return self._has_deno

    def _extract_video_id(self, url: str) -> str:
        """Extract video ID from YouTube URL."""
        if "v=" in url:
            return url.split("v=")[-1].split("&")[0]
        elif "youtu.be/" in url:
            return url.split("youtu.be/")[-1].split("?")[0]
        return url

    async def _get_invidious_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Get video info from Invidious instances (fast, no yt-dlp)."""
        # More reliable Invidious instances (some may be blocked on Koyeb)
        # Prioritize instances known to work better
        invidious_instances = [
            "https://yewtu.be",
            "https://invidious.nerdvpn.de",
            "https://invidious.snopyta.org",
            "https://invidious.projectsegfau.lt",
            "https://invidious.privacydev.net",
            "https://yewtu.be",
        ]
        
        for instance in invidious_instances:
            try:
                invidious_url = f"{instance}/api/v1/videos/{video_id}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(invidious_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            content_type = resp.headers.get("Content-Type", "")
                            if "application/json" not in content_type:
                                logger.warning("Invidious %s returned non-JSON: %s", instance, content_type)
                                continue
                            import json
                            data = await resp.json()
                            # Convert Invidious format to yt-dlp-like format
                            info = {
                                "id": video_id,
                                "title": data.get("title", "YouTube Video"),
                                "duration": data.get("length_seconds", 0),
                                "formats": [],
                            }
                            # Add format info from Invidious
                            for fmt in data.get("adaptiveFormats", []):
                                info["formats"].append({
                                    "url": fmt.get("url"),
                                    "ext": fmt.get("mimeType", "").split("/")[-1].split(";")[0],
                                    "filesize": fmt.get("contentLength"),
                                    "format_id": fmt.get("itag"),
                                    "height": fmt.get("height"),
                                    "width": fmt.get("width"),
                                    "fps": fmt.get("fps"),
                                    "vcodec": fmt.get("mimeType", "").split("/")[0] if "video" in fmt.get("mimeType", "") else "none",
                                    "acodec": fmt.get("mimeType", "").split("/")[0] if "audio" in fmt.get("mimeType", "") else "none",
                                })
                            logger.info("Got video info from Invidious: %s", instance)
                            return info
            except Exception as e:
                logger.warning("Invidious %s failed: %s", instance, e)
                continue
        return None

    async def _get_invidious_stream_url(self, video_id: str) -> Optional[str]:
        """Get direct stream URL from Invidious (fast, no yt-dlp)."""
        invidious_instances = [
            "https://yewtu.be",
            "https://invidious.nerdvpn.de",
            "https://invidious.snopyta.org",
            "https://invidious.projectsegfau.lt",
            "https://invidious.privacydev.net",
            "https://yewtu.be",
        ]
        
        for instance in invidious_instances:
            try:
                invidious_url = f"{instance}/api/v1/videos/{video_id}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(invidious_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            import json
                            data = await resp.json()
                            # Find best mp4 format
                            best_url = None
                            for fmt in data.get("adaptiveFormats", []):
                                if fmt.get("mimeType", "").startswith("video/") and fmt.get("url"):
                                    best_url = fmt["url"]
                                    break
                            if not best_url:
                                for fmt in data.get("adaptiveFormats", []):
                                    if fmt.get("url"):
                                        best_url = fmt["url"]
                                        break
                            if best_url:
                                logger.info("Got stream URL from Invidious: %s", instance)
                                return best_url
            except Exception as e:
                logger.warning("Invidious stream %s failed: %s", instance, e)
                continue
        return None

    async def get_video_info(self, url: str) -> dict:
        """Get video metadata. Try Invidious FIRST (fast), then yt-dlp as fallback."""
        video_id = self._extract_video_id(url)
        
        # Check cache first
        if video_id in self._info_cache:
            logger.info("Using cached video info for %s", video_id)
            return self._info_cache[video_id]
        
        # Try Invidious FIRST (fast, no yt-dlp subprocess)
        logger.info("Trying Invidious for video info: %s", video_id)
        info = await self._get_invidious_info(video_id)
        if info and info.get("formats"):
            self._info_cache[video_id] = info
            self._cleanup_cookies()
            return info
        
        # Fallback to yt-dlp with limited attempts
        logger.info("Invidious failed, trying yt-dlp for video info...")
        
        # Check Deno availability for SABR experiment
        has_deno = await self._check_deno()
        
        # If Deno is available, try SABR clients first; otherwise skip to web clients
        if has_deno:
            player_clients = ["tv_embedded", "android_embedded", "web_embedded", "web", "default"]
            logger.info("Deno available - trying SABR-compatible clients (tv_embedded, android_embedded)")
        else:
            player_clients = ["web_embedded", "web", "default"]
            logger.warning("Deno NOT available - skipping SABR clients (tv_embedded, android_embedded), using web clients")
        
        # Track if cookies are invalid to avoid retrying with them
        cookies_invalid = False
        
        # Try with cookies first, then without cookies if cookies fail
        for use_cookies in [True, False]:
            if not use_cookies:
                logger.info("Trying without cookies...")
            
            for player_client in player_clients:
                cmd, env = self._get_base_cmd()
                
                # Remove cookies if not using them OR if cookies are invalid
                if not use_cookies or self._cookies_invalidated:
                    try:
                        idx = cmd.index("--cookies")
                        cmd.pop(idx)
                        cmd.pop(idx)
                    except ValueError:
                        pass
                
                if player_client == "default":
                    try:
                        idx = cmd.index("--extractor-args")
                        cmd.pop(idx)
                        cmd.pop(idx)
                    except ValueError:
                        pass
                else:
                    try:
                        idx = cmd.index("--extractor-args")
                        cmd[idx+1] = f"youtube:player_client={player_client}"
                    except ValueError:
                        cmd.extend(["--extractor-args", f"youtube:player_client={player_client}"])
                
                cmd.extend(["--dump-json", "--no-download", "--skip-download", url])
                
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env
                    )
                    try:
                        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.communicate()
                        logger.warning("Video info timeout (client: %s, cookies: %s)", player_client, use_cookies)
                        continue
                    
                    if proc.returncode == 0:
                        stdout_str = stdout.decode().strip()
                        if stdout_str:
                            import json
                            info = json.loads(stdout_str)
                            logger.info("Got video info with player_client: %s (cookies: %s)", player_client, use_cookies)
                            self._info_cache[video_id] = info
                            self._cleanup_cookies()
                            return info
                    
                    stderr_str = stderr.decode()[:300]
                    logger.warning("Video info failed (client: %s, cookies: %s): %s", player_client, use_cookies, stderr_str)
                    
                    # Detect invalid cookies - stop using them
                    if ("sign in to confirm" in stderr_str.lower() 
                        or "not a bot" in stderr_str.lower() 
                        or "please sign in" in stderr_str.lower()
                        or "skipping cookie file entry due to invalid length" in stderr_str.lower()):
                        self._cookies_invalidated = True
                        # Clear cookie content so _prepare_cookies returns None for future attempts
                        self.cookie_content = None
                        # Also invalidate cookie file path so it won't be used again
                        self.cookie_file = None
                        self._cleanup_cookies()
                        logger.warning("Cookies appear invalid (bot detection or format error), cleared cookies and will skip cookie attempts")
                    
                    # If "no longer supported" or "video unavailable" error, try next client immediately
                    if "no longer supported" in stderr_str.lower() or "video is unavailable" in stderr_str.lower():
                        continue
                    
                except Exception as e:
                    logger.warning("Video info exception (client: %s, cookies: %s): %s", player_client, use_cookies, e)
        
        # Last resort: basic command (with Deno PATH for n challenge)
        logger.info("All player clients failed, trying basic command...")
        try:
            basic_cmd = [
                self.yt_dlp_path,
                "--no-check-certificates",
                "--no-warnings",
                "--ignore-errors",
                "--dump-json",
                "--no-download",
                "--skip-download",
                url
            ]
            cookie_path = self._prepare_cookies()
            if cookie_path:
                basic_cmd.extend(["--cookies", cookie_path])
            
            # Ensure Deno is in PATH for basic command too
            import os
            env = os.environ.copy()
            deno_path = "/root/.deno/bin"
            if deno_path not in env.get("PATH", ""):
                env["PATH"] = deno_path + ":" + env.get("PATH", "")
            for p in ["/usr/local/bin", "/usr/bin", "/opt/deno"]:
                if p not in env.get("PATH", ""):
                    env["PATH"] = p + ":" + env.get("PATH", "")
            
            proc = await asyncio.create_subprocess_exec(
                *basic_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logger.warning("Basic video info timeout")
            else:
                if proc.returncode == 0:
                    stdout_str = stdout.decode().strip()
                    if stdout_str:
                        import json
                        info = json.loads(stdout_str)
                        logger.info("Got video info with basic command")
                        self._info_cache[video_id] = info
                        self._cleanup_cookies()
                        return info
        except Exception as e:
            logger.warning("Basic video info exception: %s", e)
        
        self._cleanup_cookies()
        logger.error("All video info attempts failed for %s", video_id)
        # Return minimal info so streaming can still attempt
        minimal_info = {"id": video_id, "title": "YouTube Video", "formats": []}
        self._info_cache[video_id] = minimal_info
        return minimal_info

    async def get_stream_url(self, url: str, quality: str = "best[ext=mp4]/best") -> str:
        """Get direct stream URL. Try Invidious FIRST (fast), then yt-dlp as fallback."""
        video_id = self._extract_video_id(url)
        
        # Try Invidious FIRST (fast, no yt-dlp subprocess)
        logger.info("Trying Invidious for stream URL: %s", video_id)
        stream_url = await self._get_invidious_stream_url(video_id)
        if stream_url:
            self._cleanup_cookies()
            return stream_url
        
        # Fallback to yt-dlp with limited attempts
        logger.info("Invidious failed, trying yt-dlp for stream URL...")
        
        # Check Deno availability for SABR experiment
        has_deno = await self._check_deno()
        
        # If Deno is available, try SABR clients first; otherwise skip to web clients
        if has_deno:
            player_clients = ["tv_embedded", "android_embedded", "web_embedded", "web", "default"]
            logger.info("Deno available - trying SABR-compatible clients (tv_embedded, android_embedded)")
        else:
            player_clients = ["web_embedded", "web", "default"]
            logger.warning("Deno NOT available - skipping SABR clients (tv_embedded, android_embedded), using web clients")
        
        # Use explicit format IDs that work without Deno (from --list-formats)
        # These are video+audio combinations that don't require n-challenge solving
        # Also include m3u8 formats which work without n-challenge
        formats = [
            quality,
            "137+140",  # 1080p video + audio
            "136+140",  # 720p video + audio
            "135+140",  # 480p video + audio
            "134+140",  # 360p video + audio
            "133+140",  # 240p video + audio
            "160+140",  # 144p video + audio
            # m3u8/HLS formats (work without n-challenge)
            "232+140",  # 720p m3u8 video + audio
            "231+140",  # 480p m3u8 video + audio
            "230+140",  # 360p m3u8 video + audio
            "229+140",  # 240p m3u8 video + audio
            "269+140",  # 144p m3u8 video + audio
            "best[ext=mp4]/best",
            "best[protocol^=https]/best",
            "best[protocol^=m3u8]/best",
            "best",
        ]
        
        # Track if cookies are invalid to avoid retrying with them (instance variable)
        # self._cookies_invalidated is set when bot detection/format errors occur
        
        # Try with cookies first, then without cookies
        for use_cookies in [True, False]:
            if not use_cookies:
                logger.info("Trying stream URL without cookies...")
            
            for player_client in player_clients:
                for fmt in formats:
                    cmd, env = self._get_base_cmd()
                    
                    # Remove cookies if not using them OR if cookies are invalid
                    if not use_cookies or self._cookies_invalidated:
                        try:
                            idx = cmd.index("--cookies")
                            cmd.pop(idx)
                            cmd.pop(idx)
                        except ValueError:
                            pass
                    
                    if player_client == "default":
                        try:
                            idx = cmd.index("--extractor-args")
                            cmd.pop(idx)
                            cmd.pop(idx)
                        except ValueError:
                            pass
                    else:
                        try:
                            idx = cmd.index("--extractor-args")
                            cmd[idx+1] = f"youtube:player_client={player_client}"
                        except ValueError:
                            cmd.extend(["--extractor-args", f"youtube:player_client={player_client}"])
                    
                    cmd.extend(["-g", "-f", fmt, url])
                    
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            *cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=env
                        )
                        try:
                            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
                        except asyncio.TimeoutError:
                            proc.kill()
                            await proc.communicate()
                            logger.warning("Format %s (client: %s, cookies: %s) timeout", fmt, player_client, use_cookies)
                            continue
                        
                        if proc.returncode == 0:
                            urls = stdout.decode().strip().split('\n')
                            if urls and urls[0]:
                                logger.info("Got stream URL with format: %s (player_client: %s, cookies: %s)", fmt, player_client, use_cookies)
                                self._cleanup_cookies()
                                return urls[0]
                        
                        stderr_str = stderr.decode()[:300]
                        logger.warning("Format %s (client: %s, cookies: %s) failed: %s", fmt, player_client, use_cookies, stderr_str)
                        
                        # Detect invalid cookies - stop using them
                        if ("sign in to confirm" in stderr_str.lower() 
                            or "not a bot" in stderr_str.lower() 
                            or "please sign in" in stderr_str.lower()
                            or "skipping cookie file entry due to invalid length" in stderr_str.lower()):
                            self._cookies_invalidated = True
                            # Clear cookie content so _prepare_cookies returns None for future attempts
                            self.cookie_content = None
                            # Also invalidate cookie file path so it won't be used again
                            self.cookie_file = None
                            self._cleanup_cookies()
                            logger.warning("Cookies appear invalid (bot detection or format error), cleared cookies and will skip cookie attempts")
                        
                        # If "no longer supported" or "video unavailable" error, try next client immediately
                        if "no longer supported" in stderr_str.lower() or "video is unavailable" in stderr_str.lower():
                            break  # break format loop, try next client
                        
                        # If format not available, try next format
                        if "requested format is not available" in stderr_str.lower():
                            continue
                        
                    except Exception as e:
                        logger.warning("Format %s (client: %s, cookies: %s) exception: %s", fmt, player_client, use_cookies, e)
        
        self._cleanup_cookies()
        # Provide more context about the failure
        error_msg = f"All stream URL attempts failed for {video_id}. "
        if cookies_invalid:
            error_msg += "Cookies were invalid (bot detection). "
        error_msg += "Tried: Invidious (6 instances), yt-dlp with player clients [tv_embedded, android_embedded, web_embedded, web, default], formats [explicit IDs, m3u8, best]. "
        error_msg += "Check: Deno installed for n-challenge, cookies valid, network access to YouTube/Invidious."
        raise Exception(error_msg)

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
            
            timeout = aiohttp.ClientTimeout(total=300, connect=30, sock_read=30)
            
            connector = aiohttp.TCPConnector(limit=10)
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                cookie_jar=cookie_jar
            ) as session:
                async with session.get(stream_url, headers=headers) as resp:
                    if resp.status == 403:
                        # Try to get a fresh URL (URLs expire)
                        logger.warning("Got 403, stream URL may have expired, retrying...")
                        self._cleanup_cookies()
                        # Recursive retry once with fresh URL
                        stream_url = await self.get_stream_url(url, quality)
                        if not stream_url:
                            raise Exception("No stream URL found on retry")
                        logger.info("Retrying with fresh URL: %s...", stream_url[:50])
                        async with session.get(stream_url, headers=headers) as resp2:
                            if resp2.status not in (200, 206):
                                logger.error("Retry failed: %s", resp2.status)
                                raise Exception(f"Stream request failed: {resp2.status}")
                            # Add timeout for chunk reading
                            async for chunk in self._iter_chunks_with_timeout(resp2.content, chunk_size, 30.0):
                                if not chunk:
                                    continue
                                yield chunk
                            return
                    elif resp.status not in (200, 206):
                        # Log response headers for debugging
                        logger.error("Stream request failed: %s, headers: %s", resp.status, dict(resp.headers))
                        raise Exception(f"Stream request failed: {resp.status}")
                    
                    # Add timeout for chunk reading
                    async for chunk in self._iter_chunks_with_timeout(resp.content, chunk_size, 30.0):
                        if not chunk:
                            continue
                        yield chunk
        finally:
            self._cleanup_cookies()

    async def _iter_chunks_with_timeout(self, content, chunk_size: int, timeout: float):
        """Iterate chunks with per-chunk timeout to prevent infinite hanging."""
        while True:
            try:
                chunk = await asyncio.wait_for(content.read(chunk_size), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("Chunk read timeout, stopping stream")
                break
            if not chunk:
                break
            yield chunk


# Module-level singleton instance
_youtube_streamer_instance: Optional[YouTubeStreamer] = None

async def get_youtube_streamer() -> YouTubeStreamer:
    """Get or create YouTube streamer instance (singleton)."""
    global _youtube_streamer_instance
    if _youtube_streamer_instance is None:
        _youtube_streamer_instance = YouTubeStreamer()
    return _youtube_streamer_instance