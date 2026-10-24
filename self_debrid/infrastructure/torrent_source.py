"""Bounded v1 metainfo validation and hashing without decoding torrent payloads."""

import hashlib

from self_debrid.domain.torrents import TorrentError, relative_parts

MAX_TORRENT_BYTES = 4 * 1024 * 1024


def metainfo_hash(payload: bytes) -> str:
    """Hash the original bencoded info slice, never a re-encoded dictionary.

    Reject duplicate keys, excessive nesting/counts, trailing data and v2-only
    metainfo. Paths and file indices are subsequently verified via the client.
    """
    if not payload or len(payload) > MAX_TORRENT_BYTES:
        raise TorrentError("source_too_large" if payload else "source_invalid")
    cursor = 0
    nodes = 0
    info_slice = None

    def parse(depth: int = 0):
        nonlocal cursor, nodes, info_slice
        nodes += 1
        if depth > 32 or nodes > 100000 or cursor >= len(payload):
            raise ValueError
        token = payload[cursor : cursor + 1]
        if token == b"i":
            end = payload.index(b"e", cursor + 1)
            value = payload[cursor + 1 : end]
            if not 1 <= len(value) <= 20 or (value.startswith(b"-") and value == b"-0"):
                raise ValueError
            number = int(value)
            if str(number).encode() != value:
                raise ValueError
            cursor = end + 1
            return number
        if token in (b"l", b"d"):
            dictionary = token == b"d"
            value = {} if dictionary else []
            cursor += 1
            while payload[cursor : cursor + 1] != b"e":
                key = parse(depth + 1) if dictionary else None
                if dictionary and (not isinstance(key, bytes) or key in value):
                    raise ValueError
                start = cursor
                item = parse(depth + 1)
                if dictionary:
                    value[key] = item
                    if depth == 0 and key == b"info":
                        info_slice = payload[start:cursor]
                else:
                    value.append(item)
            cursor += 1
            return value
        end = payload.index(b":", cursor)
        length_text = payload[cursor:end]
        if not 1 <= len(length_text) <= 8 or not length_text.isdigit():
            raise ValueError
        length = int(length_text)
        if str(length).encode() != length_text or end + 1 + length > len(payload):
            raise ValueError
        cursor = end + 1 + length
        return payload[end + 1 : cursor]

    try:
        root = parse()
        info = root[b"info"]
        if cursor != len(payload) or not isinstance(info, dict) or not info_slice:
            raise ValueError
        pieces = info.get(b"pieces")
        if not isinstance(pieces, bytes) or not pieces or len(pieces) % 20:
            raise ValueError
        if type(info.get(b"piece length")) is not int or info[b"piece length"] <= 0:
            raise ValueError
        if not isinstance(info.get(b"name"), bytes) or not info[b"name"]:
            raise ValueError
        for name_key in (b"name", b"name.utf-8"):
            if name_key in info:
                name = info[name_key].decode("utf-8")
                if len(relative_parts(name)) != 1:
                    raise ValueError
        if b"files" in info:
            if (
                b"length" in info
                or not isinstance(info[b"files"], list)
                or not 1 <= len(info[b"files"]) <= 10000
            ):
                raise ValueError
            total = 0
            for file in info[b"files"]:
                if (
                    not isinstance(file, dict)
                    or type(file.get(b"length")) is not int
                    or file[b"length"] < 0
                ):
                    raise ValueError
                if file.get(b"symlink path") is not None or b"l" in file.get(
                    b"attr", b""
                ):
                    raise ValueError
                total += file[b"length"]
                for key in (b"path", b"path.utf-8"):
                    if key == b"path.utf-8" and key not in file:
                        continue
                    path = file[key]
                    if (
                        not isinstance(path, list)
                        or not path
                        or any(not isinstance(part, bytes) or not part for part in path)
                    ):
                        raise ValueError
                    parts = [part.decode("utf-8") for part in path]
                    if any(len(relative_parts(part)) != 1 for part in parts):
                        raise ValueError
        else:
            total = info.get(b"length")
            if type(total) is not int or total < 0:
                raise ValueError
        expected_pieces = (total + info[b"piece length"] - 1) // info[b"piece length"]
        if expected_pieces != len(pieces) // 20:
            raise ValueError
        return hashlib.sha1(info_slice).hexdigest()
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        RecursionError,
        AttributeError,
    ):
        raise TorrentError("source_invalid") from None
