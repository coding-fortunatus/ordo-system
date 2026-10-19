"""Calling a school's portal (spec 0005, *Key invariants*).

Every request resolves the portal's host itself and refuses any address that
isn't public, then connects to the address it checked, so a DNS answer that
changes between the check and the call can't reach an internal service.
Redirects are not followed. A run shares one byte budget across all its
requests; any cap hit is a ``FetchError``, and a sync with a ``FetchError``
applies nothing.

Errors name the portal's host only: never the URL's query, a header, the
secret or response content.
"""
import csv
import io
import ipaddress
import json
import socket
import time
from dataclasses import dataclass, field

import httpx
from django.conf import settings

from ems.portal_sync import secrets

TIMEOUT_SECONDS = 30
RETRY_DELAYS = (1, 4, 16)
MAX_PAGES = 1_000
MAX_BYTES = 50 * 1024 * 1024
MAX_ROWS = 200_000

# Swapped out by tests so retries don't sleep.
_sleep = time.sleep


class FetchError(Exception):
    """A portal request that failed for a reason the admin can act on.

    ``reason`` is one of: ``timeout``, ``connection_error``, ``http_status``,
    ``not_parseable``, ``rows_path_not_list``, ``address_blocked``,
    ``too_large``, ``too_many_rows``, ``too_many_pages``, ``bad_next_link``.
    """

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason
        self.message = message


class _Retryable(FetchError):
    pass


@dataclass
class Budget:
    """Bytes left for the whole run (50 MB, AC-6)."""

    remaining: int = MAX_BYTES


@dataclass
class Page:
    rows: list
    data: object = field(default=None, repr=False)


def check_url(url):
    """The parsed URL, or ``FetchError`` when its scheme isn't allowed:
    https only, plus http while ``DEBUG``."""
    try:
        parsed = httpx.URL(url)
    except Exception as exc:
        raise FetchError("address_blocked", "The address is not a valid URL.") from exc
    allowed = ("https", "http") if settings.DEBUG else ("https",)
    if parsed.scheme not in allowed or not parsed.host:
        raise FetchError(
            "address_blocked", "The address must be an https:// URL with a host name."
        )
    return parsed


def _is_public(ip):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def resolve_public(host, port):
    """One public IP for ``host``. Refuses the whole host if any address it
    resolves to is loopback, private, link local or reserved."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise _Retryable(
            "connection_error", f"Could not find the portal host {host}."
        ) from exc
    ips = [ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos]
    if not ips or not all(_is_public(ip) for ip in ips):
        raise FetchError(
            "address_blocked",
            f"The portal host {host} points at a private or reserved address, "
            "which Ordo won't call.",
        )
    return str(ips[0])


def auth_headers(connection):
    """The portal's auth header. The secret is decrypted here, for the
    request only."""
    secret = secrets.reveal(connection)
    if connection.auth_type == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    return {connection.header_name: secret}


def _get_once(client, url, headers, budget):
    host = url.host
    port = url.port or (443 if url.scheme == "https" else 80)
    ip = resolve_public(host, port)
    pinned = url.copy_with(host=ip)
    request_headers = {**headers, "Host": url.netloc.decode("ascii")}
    try:
        with client.stream(
            "GET",
            pinned,
            headers=request_headers,
            extensions={"sni_hostname": host},
        ) as response:
            status = response.status_code
            if status >= 500:
                raise _Retryable("http_status", f"The portal at {host} answered HTTP {status}.")
            if status != 200:
                raise FetchError("http_status", f"The portal at {host} answered HTTP {status}.")
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > budget.remaining:
                    raise FetchError(
                        "too_large",
                        f"The portal sent more than {MAX_BYTES // (1024 * 1024)} MB "
                        "in one sync.",
                    )
            budget.remaining -= len(body)
            return bytes(body)
    except httpx.TimeoutException as exc:
        raise _Retryable(
            "timeout", f"The portal at {host} took longer than {TIMEOUT_SECONDS} s to answer."
        ) from exc
    except httpx.TransportError as exc:
        raise _Retryable("connection_error", f"Could not connect to the portal at {host}.") from exc


def get(client, url, headers, budget, *, retry=True):
    """GET one page's body. Retries timeouts, connection errors and 5xx
    with backoff when ``retry``."""
    delays = RETRY_DELAYS if retry else ()
    for attempt in range(len(delays) + 1):
        try:
            return _get_once(client, url, headers, budget)
        except _Retryable as exc:
            if attempt == len(delays):
                raise FetchError(exc.reason, exc.message) from None
            _sleep(delays[attempt])


def _at(data, path):
    """The value at dotted ``path`` in ``data`` (``data`` itself when the
    path is empty), or None."""
    node = data
    for part in [p for p in (path or "").split(".") if p]:
        node = node.get(part) if isinstance(node, dict) else None
    return node


def parse_json_rows(body, rows_path):
    """The list of row objects at ``rows_path`` (dotted; empty for a bare
    list) in a JSON body."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise FetchError("not_parseable", "The portal's answer is not valid JSON.") from None
    node = _at(data, rows_path)
    where = f"at '{rows_path}'" if rows_path else "at the top level"
    if not isinstance(node, list):
        raise FetchError("rows_path_not_list", f"The answer has no list of rows {where}.")
    if not all(isinstance(row, dict) for row in node):
        raise FetchError(
            "rows_path_not_list", f"The rows {where} are not all objects (name: value)."
        )
    return Page(rows=node, data=data)


def parse_csv_rows(body):
    """One row per line under the header row, keyed by its column names,
    read as the CSV uploads read files (UTF-8, comma separated). Cells past
    the header's last column are dropped."""
    try:
        text = body.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""))
        header = next(reader, None)
        records = list(reader)
    except (UnicodeDecodeError, csv.Error):
        raise FetchError(
            "not_parseable", "The portal's answer is not a UTF-8 CSV file."
        ) from None
    if not header or not any(h.strip() for h in header):
        raise FetchError("not_parseable", "The portal's CSV has no header row.")
    names = [h.strip() for h in header]
    rows = [
        {name: value for name, value in zip(names, record) if name}
        for record in records
        if any(v.strip() for v in record)
    ]
    return Page(rows=rows)


def _client():
    return httpx.Client(
        timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False
    )


def _next_url(page, endpoint, url, start):
    """The next page's URL from ``next_path``, or None when it is empty.
    Only the endpoint's own host, by https (http while ``DEBUG``)."""
    link = _at(page.data, endpoint.next_path)
    if link in (None, ""):
        return None
    if not isinstance(link, str):
        raise FetchError(
            "bad_next_link", f"The value at '{endpoint.next_path}' is not a page link."
        )
    try:
        nxt = check_url(str(url.join(link.strip())))
    except FetchError:
        raise FetchError(
            "bad_next_link", f"The next page link at '{endpoint.next_path}' is not a usable address."
        ) from None
    if nxt.host != start.host or nxt.port != start.port:
        raise FetchError(
            "address_blocked",
            f"The next page link leaves the portal at {start.host}, which Ordo won't follow.",
        )
    return nxt


def _pages(client, endpoint, headers, budget, retry):
    """Each page of ``endpoint`` in order, as ``Page``s: one for csv or
    no paging; ``page_param`` counts from 1 until a page has no rows;
    ``next_link`` follows ``next_path`` until it is empty."""
    start = check_url(endpoint.url)
    if endpoint.format == "csv":
        yield parse_csv_rows(get(client, start, headers, budget, retry=retry))
        return
    if endpoint.pagination == "page_param":
        for n in range(1, MAX_PAGES + 1):
            url = start.copy_set_param(endpoint.page_param, str(n))
            page = parse_json_rows(get(client, url, headers, budget, retry=retry), endpoint.rows_path)
            if not page.rows:
                return
            yield page
        raise _too_many_pages()
    url, visited = start, set()
    for _ in range(MAX_PAGES):
        visited.add(str(url))
        page = parse_json_rows(get(client, url, headers, budget, retry=retry), endpoint.rows_path)
        yield page
        if endpoint.pagination != "next_link":
            return
        url = _next_url(page, endpoint, url, start)
        if url is None:
            return
        if str(url) in visited:
            raise FetchError(
                "bad_next_link", "The portal's next page links go round in a loop."
            )
    raise _too_many_pages()


def _too_many_pages():
    return FetchError(
        "too_many_pages", f"The portal sent more than {MAX_PAGES:,} pages for one endpoint."
    )


def fetch_rows(endpoint, connection, *, budget=None, first_page_only=False, retry=True):
    """Every row of ``endpoint`` across all its pages, or the first page's
    rows when ``first_page_only``. Raises ``FetchError``."""
    budget = budget or Budget()
    headers = auth_headers(connection)
    rows = []
    with _client() as client:
        for page in _pages(client, endpoint, headers, budget, retry):
            rows.extend(page.rows)
            if len(rows) > MAX_ROWS:
                raise FetchError(
                    "too_many_rows",
                    f"The portal sent more than {MAX_ROWS:,} rows for one endpoint.",
                )
            if first_page_only:
                break
    return rows


def cell(value):
    """A row value as stripped text. JSON numbers become strings; ``null``
    becomes blank; a nested value becomes its JSON text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value).strip()


def columns(rows):
    """The union of the rows' keys, in first seen order."""
    seen = {}
    for row in rows:
        for key in row:
            seen.setdefault(str(key), None)
    return list(seen)
