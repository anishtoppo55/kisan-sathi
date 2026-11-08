"""Browser audio (webm/opus, ogg, mp4…) -> 16 kHz mono WAV for Prisma."""

import asyncio

MAX_SECONDS = 29  # Prisma REST limit is 30 s


class AudioConversionError(Exception):
    pass


async def to_wav16k(data: bytes, timeout: float = 20.0) -> bytes:
    if not data:
        raise AudioConversionError("empty audio")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-t", str(MAX_SECONDS), "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
        "-f", "wav", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(data), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise AudioConversionError("ffmpeg timed out")
    if proc.returncode != 0 or len(out) <= 44:  # 44 = bare WAV header
        raise AudioConversionError(f"ffmpeg failed: {err.decode(errors='ignore')[:200]}")
    return out
