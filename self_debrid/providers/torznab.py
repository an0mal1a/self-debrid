"""Asynchronous, capability-aware Torznab/Prowlarr discovery; no scraping."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, ParseError

import aiohttp
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from self_debrid.domain import MediaRequest, MediaType, StreamCandidate
from self_debrid.domain.identity import (
    magnet_info_hash,
    normalize_info_hash,
    normalize_url,
)
from self_debrid.metadata.release_parser import nonnegative_integer, parse_release
from self_debrid.providers.base import ProviderError

MAX_XML_BYTES = 2 * 1024 * 1024
TORZNAB_NAMESPACE = "http://torznab.com/schemas/2015/feed"


@dataclass(frozen=True, slots=True)
class TorznabCapabilities:
    movie: frozenset[str] | None = None
    tv: frozenset[str] | None = None
    search: frozenset[str] | None = None


def _local_name(element: Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _parse_xml(data: bytes) -> Element:
    if len(data) > MAX_XML_BYTES:
        raise ProviderError(
            "response_too_large", "Torznab XML exceeds response size limit"
        )
    try:
        root = ElementTree.fromstring(data, forbid_dtd=True)
    except (ParseError, DefusedXmlException, ValueError):
        raise ProviderError(
            "invalid_xml", "Torznab returned malformed or unsafe XML"
        ) from None
    error = next((node for node in root.iter() if _local_name(node) == "error"), None)
    if error is not None:
        code = error.get("code", "")
        suffix = f" (code {code})" if re.fullmatch(r"[0-9]{1,4}", code) else ""
        # A remote description may echo the API key; never include it.
        raise ProviderError("torznab_error", "Torznab API error" + suffix)
    return root


def parse_capabilities(data: bytes) -> TorznabCapabilities:
    root = _parse_xml(data)
    if _local_name(root) != "caps":
        raise ProviderError("invalid_capabilities", "Expected a Torznab caps document")
    searching = next((node for node in root if _local_name(node) == "searching"), None)
    if searching is None:
        raise ProviderError(
            "invalid_capabilities", "Torznab caps has no searching element"
        )
    modes = {}
    for node in searching:
        if node.get("available", "no").lower() != "yes":
            continue
        modes[_local_name(node)] = frozenset(
            value.strip().lower()
            for value in node.get("supportedParams", "").split(",")
            if value.strip()
        )
    return TorznabCapabilities(
        movie=modes.get("movie-search"),
        tv=modes.get("tv-search"),
        search=modes.get("search"),
    )


def build_search_params(
    request: MediaRequest, caps: TorznabCapabilities
) -> dict[str, str]:
    """Use advertised parameters only; text fallback needs an explicit title."""
    params = caps.movie if request.media_type is MediaType.MOVIE else caps.tv
    mode = "movie" if request.media_type is MediaType.MOVIE else "tvsearch"
    coordinates = {"season", "ep"}
    episode_request = request.media_type is MediaType.SERIES
    if params is not None:
        if "imdbid" in params and (not episode_request or coordinates <= params):
            result = {"t": mode, "imdbid": request.imdb_id[2:]}
        elif request.title and "q" in params:
            result = {"t": mode, "q": request.title}
        else:
            result = {}
        if result:
            if episode_request:
                if coordinates <= params:
                    result.update(season=str(request.season), ep=str(request.episode))
                else:
                    result["q"] += f" S{request.season:02d}E{request.episode:02d}"
            return result
    if request.title and caps.search is not None and "q" in caps.search:
        title = request.title
        if episode_request:
            title += f" S{request.season:02d}E{request.episode:02d}"
        return {"t": "search", "q": title}
    raise ProviderError(
        "unsupported_search",
        "Indexer cannot search this request with advertised capabilities; a title may enable text search",
    )


def _item_urls(item: Element, attrs: dict[str, list[str]]) -> list[str]:
    magnets = list(attrs.get("magneturl", []))
    enclosures = []
    links = []
    for node in item:
        name = _local_name(node)
        if name == "enclosure" and node.get("type") in (
            None,
            "application/x-bittorrent",
            "application/x-torrent",
            "application/x-magnet",
        ):
            enclosures.append(node.get("url", ""))
        elif name == "link":
            links.append(node.text or "")
        elif name == "guid" and (node.text or "").strip().lower().startswith("magnet:"):
            magnets.append(node.text.strip())
    # An enclosure is a download locator; an ordinary link may be a detail page.
    urls = []
    for url in magnets + enclosures + links:
        try:
            url = normalize_url(url)
            if url.startswith("magnet:") and magnet_info_hash(url) is None:
                continue
            urls.append(url)
        except ValueError:
            continue
    return urls


def _parse_item(item: Element, provider: str) -> StreamCandidate:
    attrs: dict[str, list[str]] = {}
    for node in item:
        if node.tag == f"{{{TORZNAB_NAMESPACE}}}attr" and node.get("name"):
            attrs.setdefault(node.get("name").lower(), []).append(node.get("value", ""))
    # Language is multivalued. Other optional metadata uses the first attribute.
    flat = {name: values[0] for name, values in attrs.items()}
    if "language" in attrs:
        flat["language"] = " ".join(attrs["language"])
    title = next(
        (
            node.text.strip()
            for node in item
            if _local_name(node) == "title" and node.text and node.text.strip()
        ),
        None,
    )
    if title is None:
        raise ProviderError("invalid_item", "Torznab item has no release title")
    urls = _item_urls(item, attrs)
    info_hash = None
    for raw_hash in attrs.get("infohash", []):
        try:
            info_hash = normalize_info_hash(raw_hash)
            break
        except ValueError:
            continue
    if info_hash is None:
        info_hash = next(
            (value for url in urls if (value := magnet_info_hash(url))), None
        )
    url = next(iter(urls), None)
    if info_hash is None and url is None:
        raise ProviderError(
            "invalid_item", "Torznab item has no usable torrent identity or URL"
        )
    metadata = parse_release(title, flat)
    size = metadata.size_bytes
    if size is None:
        size = next(
            (
                value
                for node in item
                if _local_name(node) == "enclosure"
                and (value := nonnegative_integer(node.get("length"))) is not None
            ),
            None,
        )
    try:
        return StreamCandidate(
            provider=provider,
            title=title,
            info_hash=info_hash,
            url=url,
            file_index=nonnegative_integer(flat.get("fileindex")),
            size_bytes=size,
            seeders=metadata.seeders,
            resolution=metadata.resolution,
            codec=metadata.codec,
            hdr=metadata.hdr,
            languages=metadata.languages,
            season=metadata.season,
            episode=metadata.episode,
        )
    except ValueError:
        raise ProviderError(
            "invalid_item", "Torznab item has inconsistent torrent metadata"
        ) from None


def parse_results(data: bytes, provider: str) -> tuple[StreamCandidate, ...]:
    root = _parse_xml(data)
    channels = [node for node in root if _local_name(node) == "channel"]
    if _local_name(root) != "rss" or not channels:
        raise ProviderError("invalid_response", "Expected a Torznab RSS channel")
    return tuple(
        _parse_item(item, provider)
        for channel in channels
        for item in channel
        if _local_name(item) == "item"
    )


class TorznabProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str = "",
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
        capabilities: TorznabCapabilities | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("Torznab base URL must be an HTTP(S) endpoint")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError(
                "Torznab base URL must not contain credentials, query or fragment"
            )
        if not name.strip():
            raise ValueError("provider name must not be blank")
        if any(
            not math.isfinite(value) or value <= 0
            for value in (connect_timeout, read_timeout)
        ):
            raise ValueError("Torznab timeouts must be finite and positive")
        self._name = name
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(
            total=connect_timeout + read_timeout,
            connect=connect_timeout,
            sock_connect=connect_timeout,
            sock_read=read_timeout,
        )
        self._capabilities = capabilities
        self._caps_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._name

    async def search(self, request: MediaRequest) -> tuple[StreamCandidate, ...]:
        if self._capabilities is None:
            async with self._caps_lock:
                if self._capabilities is None:
                    self._capabilities = parse_capabilities(
                        await self._fetch({"t": "caps"})
                    )
        return parse_results(
            await self._fetch(build_search_params(request, self._capabilities)),
            self.name,
        )

    async def _fetch(self, params: dict[str, str]) -> bytes:
        params = {**params, "apikey": self._api_key}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(
                    self._base_url, params=params, allow_redirects=False
                ) as response:
                    if response.status != 200:
                        raise ProviderError(
                            "http_error", f"Torznab HTTP status {response.status}"
                        )
                    chunks = []
                    size = 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > MAX_XML_BYTES:
                            raise ProviderError(
                                "response_too_large",
                                "Torznab XML exceeds response size limit",
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
        except asyncio.TimeoutError:
            raise ProviderError(
                "timeout", "Torznab connection or read timed out"
            ) from None
        except aiohttp.ClientError:
            raise ProviderError(
                "connection_error", "Torznab connection failed"
            ) from None
