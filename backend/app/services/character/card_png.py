"""Read character-card text chunks without executing code or fetching resources."""

import base64
import json
import struct
import zlib

MAX_CARD_JSON = 256 * 1024
MAX_CARD_FILE = 10 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def limited_decompress(data: bytes) -> bytes:
    decoder = zlib.decompressobj()
    try:
        result = decoder.decompress(data, MAX_CARD_JSON * 2 + 1)
    except zlib.error as exc:
        raise ValueError("角色卡压缩文本已损坏") from exc
    if len(result) > MAX_CARD_JSON * 2 or decoder.unconsumed_tail or not decoder.eof:
        raise ValueError("角色卡压缩文本过大或已损坏")
    return result


def read_png_card(raw: bytes) -> dict:
    if not raw.startswith(PNG_SIGNATURE) or len(raw) > MAX_CARD_FILE:
        raise ValueError("请选择不超过 10 MB 的 PNG 角色卡")
    position = 8
    cards = {}
    first = True
    ended = False
    while position + 12 <= len(raw):
        length = struct.unpack_from(">I", raw, position)[0]
        kind = raw[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(raw):
            raise ValueError("PNG 文件不完整")
        data = raw[position + 8 : position + 8 + length]
        crc = struct.unpack_from(">I", raw, position + 8 + length)[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != crc:
            raise ValueError("PNG 校验失败")
        if first:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG 图片头无效")
            width, height = struct.unpack_from(">II", data)
            if not width or not height or width > 4096 or height > 4096:
                raise ValueError("角色卡图片尺寸须在 4096 × 4096 以内")
            first = False
        keyword = b""
        value = b""
        if kind == b"tEXt":
            keyword, separator, value = data.partition(b"\0")
            if not separator:
                raise ValueError("PNG 文本块无效")
        elif kind == b"zTXt":
            keyword, separator, tail = data.partition(b"\0")
            if keyword in {b"chara", b"ccv3"}:
                if not separator or not tail or tail[0] != 0:
                    raise ValueError("PNG 压缩文本块无效")
                value = limited_decompress(tail[1:])
        elif kind == b"iTXt":
            keyword, separator, tail = data.partition(b"\0")
            if keyword in {b"chara", b"ccv3"}:
                if not separator or len(tail) < 2 or tail[0] not in {0, 1} or tail[1] != 0:
                    raise ValueError("PNG 国际文本块无效")
                fields = tail[2:].split(b"\0", 2)
                if len(fields) != 3:
                    raise ValueError("PNG 国际文本块不完整")
                value = limited_decompress(fields[2]) if tail[0] else fields[2]
        if keyword in {b"chara", b"ccv3"}:
            if len(value) > MAX_CARD_JSON * 2:
                raise ValueError("角色卡文本超过 256 KB")
            try:
                decoded = base64.b64decode(value, validate=True)
                if len(decoded) > MAX_CARD_JSON:
                    raise ValueError("角色卡文本超过 256 KB")
                cards[keyword] = json.loads(decoded.decode("utf-8-sig"))
            except (UnicodeError, ValueError, RecursionError) as exc:
                raise ValueError("PNG 角色卡内的 JSON 数据无效") from exc
        position = end
        if kind == b"IEND":
            if length or position != len(raw):
                raise ValueError("PNG 结束块无效")
            ended = True
            break
    if not ended or not cards:
        raise ValueError("图片中没有 Tavern V2/V3 角色卡数据；普通头像请使用自定义角色")
    card = cards.get(b"ccv3", cards.get(b"chara"))
    if not isinstance(card, dict):
        raise ValueError("角色卡必须包含 JSON 对象")
    return card
