"""Local copies of the uploaded files: data/images/<year>/<month>/<job id>-<file unique id>.<ext>.

The worker downloads the file on the job's first step (not the Telegram handler), so a download
failure is just a retry with backoff. The path is saved in the job; a missing copy is downloaded again.
"""

import asyncio
from pathlib import Path, PurePosixPath
from typing import Protocol

from docbot.codes import sniff_mime
from docbot.db.models import Job
from docbot.errors import PermanentError
from docbot.pipeline.worker import JobContext

_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "application/pdf": ".pdf",
}


class FileSource(Protocol):
    async def download(self, file_id: str, destination: Path) -> None:
        """Save the file to ``destination``. TransientError / PermanentError on failure."""


def extension(mime: str | None, file_name: str | None = None) -> str:
    if mime in _EXTENSIONS:
        return _EXTENSIONS[mime]
    suffix = PurePosixPath(file_name or "").suffix.lower()
    return suffix if 1 < len(suffix) <= 6 else ".bin"


class FileStore:
    def __init__(self, root: Path, source: FileSource, *, max_bytes: int):
        self.root = root
        self._source = source
        self._max_bytes = max_bytes

    @staticmethod
    def relative_path(job: Job) -> str:
        return f"{job.created_at:%Y/%m}/{job.id}-{job.file_unique_id}{extension(job.mime_type, job.file_name)}"

    async def load(self, ctx: JobContext) -> bytes:
        """The job's file content, downloading it first if there is no local copy yet."""
        job = ctx.job
        if job.path:
            path = self.root / job.path
            if path.is_file():
                return await asyncio.to_thread(path.read_bytes)
        relative = self.relative_path(job)
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        await self._source.download(job.file_id, partial)
        data = await asyncio.to_thread(partial.read_bytes)
        if len(data) > self._max_bytes:
            partial.unlink(missing_ok=True)
            raise PermanentError(f"fayl juda katta: {len(data) // 1024 // 1024} MB")
        partial.replace(target)
        await ctx.update(path=relative, file_size=len(data), mime_type=sniff_mime(data, job.mime_type))
        return data

    def delete(self, relative: str) -> None:
        (self.root / relative).unlink(missing_ok=True)
