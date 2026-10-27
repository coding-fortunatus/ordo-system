"""Build large exports in a temp file and stream them out in chunks.

A zip of attendance sheets for a busy slot runs to tens of megabytes. Built
in a ``BytesIO`` and returned through ``HttpResponse(buf.getvalue())`` it
sits in memory two or three times over for the whole request. Here it goes
into a :func:`spool`, which stays in memory while small and moves to a temp
file on disk past :data:`SPOOL_MAX_BYTES`, and :class:`SpooledFileResponse`
sends it in chunks.

Django's own ``FileResponse`` does not help under Daphne: on Django 5.0 an
ASGI server reads a sync streaming response whole into a list before
sending the first byte. :class:`SpooledFileResponse` gives the ASGI handler
an async iterator that reads the file one chunk at a time, off the shared
sync thread, and keeps the sync iterator for WSGI and the test client.
"""

import tempfile

from asgiref.sync import sync_to_async
from django.http import StreamingHttpResponse

# Above this, the spool moves from memory to a temp file on disk.
SPOOL_MAX_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 64 * 1024

ZIP = "application/zip"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def spool():
    """A binary file to build an export in; pass it to :class:`SpooledFileResponse`."""
    return tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES, mode="w+b")


def _chunks(f):
    while chunk := f.read(CHUNK_BYTES):
        yield chunk


class SpooledFileResponse(StreamingHttpResponse):
    """Stream ``f`` (rewound) as an attachment named ``filename``; closes it after."""

    def __init__(self, f, filename, content_type):
        f.seek(0, 2)
        size = f.tell()
        f.seek(0)
        super().__init__(_chunks(f), content_type=content_type)
        self._file = f
        self._resource_closers.append(f.close)
        self["Content-Length"] = str(size)
        self["Content-Disposition"] = f'attachment; filename="{filename}"'

    async def __aiter__(self):
        # Not thread sensitive: Django runs every sync view on one shared
        # thread, and a slow download must not hold it between chunks.
        read = sync_to_async(self._file.read, thread_sensitive=False)
        while chunk := await read(CHUNK_BYTES):
            yield chunk


def counted_export(kind, response):
    """Count a finished export and its bytes (spec 0006, AC-9), and return
    the response. A streamed one counts the length it declares, if any."""
    from tenancy.observability import metrics

    if response.status_code < 300:
        size = response.get("Content-Length")
        if size is None and not response.streaming:
            size = len(response.content)
        metrics.EXPORTS.add(1, kind=kind)
        if size:
            metrics.EXPORT_BYTES.add(int(size), kind=kind)
    return response
