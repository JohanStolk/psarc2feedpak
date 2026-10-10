"""Lossless Wwise audio: repair the Ogg stream ww2ogg rebuilds from a .wem.

ww2ogg turns Wwise's stripped Vorbis back into a standard Ogg Vorbis stream
without re-encoding, but the granule positions it writes are not reliable, so
players disagree on the length and seeking drifts. This is what the classic
`revorb` tool fixes. Doing it here avoids a second native binary:

  1. read the Vorbis packets back out of ww2ogg's pages,
  2. work out how many samples each audio packet contributes (from the
     blocksizes in the identification header and the mode blockflags at the
     end of the setup header),
  3. write fresh pages with correct granules, ending at the .wem's own
     sample count so the encoder padding at the end is trimmed.

The packets themselves are copied byte for byte.
"""

import struct

# ── Ogg CRC (polynomial 0x04C11DB7, no reflection, unlike zlib's crc32) ──
_CRC = []
for _i in range(256):
    _r = _i << 24
    for _ in range(8):
        _r = ((_r << 1) ^ 0x04C11DB7) if _r & 0x80000000 else (_r << 1)
    _CRC.append(_r & 0xFFFFFFFF)


def _crc(data):
    c = 0
    for b in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ _CRC[(c >> 24) ^ b]
    return c


def read_packets(data):
    """All packets of the first logical stream in an Ogg file, in order."""
    packets, cur, pos, serial = [], b"", 0, None
    while pos + 27 <= len(data):
        if data[pos:pos + 4] != b"OggS":
            raise ValueError(f"bad Ogg page at byte {pos}")
        nseg = data[pos + 26]
        page_serial = struct.unpack_from("<I", data, pos + 14)[0]
        lacing = data[pos + 27:pos + 27 + nseg]
        body = pos + 27 + nseg
        if serial is None:
            serial = page_serial
        if page_serial == serial:
            for lace in lacing:
                cur += data[body:body + lace]
                body += lace
                if lace < 255:
                    packets.append(cur)
                    cur = b""
        pos = pos + 27 + nseg + sum(lacing)
    if cur:
        packets.append(cur)
    return packets


def _blocksizes(ident):
    if ident[:7] != b"\x01vorbis":
        raise ValueError("first packet is not a Vorbis identification header")
    b = ident[28]
    return 1 << (b & 0x0F), 1 << (b >> 4)


def _mode_blockflags(setup):
    """Blockflag of each mode, parsed backwards from the end of the setup header.

    Each mode is blockflag(1) windowtype(16)=0 transformtype(16)=0 mapping(8),
    followed by a framing bit. Walking back from the framing bit and checking
    the zero fields avoids decoding the codebooks/floors/residues before them
    (the same trick ffmpeg's vorbis_parser uses).
    """
    if setup[:7] != b"\x05vorbis":
        raise ValueError("third packet is not a Vorbis setup header")
    bits = [(byte >> i) & 1 for byte in setup for i in range(8)]  # LSB first
    end = max(i for i, v in enumerate(bits) if v)                 # framing bit

    def field(start, width):
        return sum(bits[start + i] << i for i in range(width))

    flags, best, p = [], None, end
    while p - 41 >= 7 * 8:
        p -= 41
        if field(p + 1, 32) != 0:          # window + transform type must be 0
            break
        flags.insert(0, bits[p])
        if p >= 6 and field(p - 6, 6) + 1 == len(flags):
            best = list(flags)             # count field agrees: a valid split
    if not best:
        raise ValueError("could not locate the Vorbis mode table")
    return best


def wem_sample_count(wem):
    """Sample count stored in a Wwise RIFF/RIFX .wem header, or None."""
    if wem[:4] not in (b"RIFF", b"RIFX"):
        return None
    e = "<" if wem[:4] == b"RIFF" else ">"
    pos, fmt = 12, None
    while pos + 8 <= len(wem):
        cid = wem[pos:pos + 4]
        size = struct.unpack_from(e + "I", wem, pos + 4)[0]
        if cid == b"vorb":
            return struct.unpack_from(e + "I", wem, pos + 8)[0]
        if cid == b"fmt ":
            fmt = (pos + 8, size)
        pos += 8 + size + (size & 1)
    if fmt and fmt[1] == 0x42:             # vorb data folded into fmt
        return struct.unpack_from(e + "I", wem, fmt[0] + 0x18)[0]
    return None


def _page(serial, seq, granule, packets, flags):
    lacing = bytearray()
    for p in packets:
        n = len(p)
        lacing += b"\xff" * (n // 255) + bytes([n % 255])
    head = struct.pack("<4sBBqIII", b"OggS", 0, flags, granule, serial, seq, 0)
    page = bytearray(head + bytes([len(lacing)]) + lacing + b"".join(packets))
    struct.pack_into("<I", page, 22, _crc(page))
    return bytes(page)


def rebuild(ogg_bytes, total_samples=None, serial=0x46424B21, page_bytes=4096):
    """Re-paginate a Vorbis Ogg stream with correct granule positions.

    Returns (new_ogg_bytes, sample_count). Packets are kept as-is.
    """
    pk = read_packets(ogg_bytes)
    if len(pk) < 4:
        raise ValueError("no audio packets")
    bs = _blocksizes(pk[0])
    flags = _mode_blockflags(pk[2])
    mode_bits = max(1, (len(flags) - 1).bit_length())

    granules, total, prev = [], 0, None
    for p in pk[3:]:
        if not p or p[0] & 1:
            raise ValueError("not an audio packet")
        mode = (p[0] >> 1) & ((1 << mode_bits) - 1)
        if mode >= len(flags):
            raise ValueError(f"packet uses undefined mode {mode}")
        cur = bs[flags[mode]]
        if prev is not None:
            total += prev // 4 + cur // 4
        prev = cur
        granules.append(total)
    if total_samples is not None:
        if total_samples > total:
            raise ValueError(f"stream has {total} samples, header says {total_samples}")
        total = total_samples

    out = [_page(serial, 0, 0, [pk[0]], 0x02),
           _page(serial, 1, 0, pk[1:3], 0x00)]
    def laces(p):
        return len(p) // 255 + 1

    seq, batch, size, nlace, g = 2, [], 0, 0, 0
    for i, p in enumerate(pk[3:]):
        # A page holds at most 255 lacing values; keep pages near page_bytes.
        if batch and (nlace + laces(p) > 255 or size >= page_bytes):
            out.append(_page(serial, seq, g, batch, 0x00))
            seq, batch, size, nlace = seq + 1, [], 0, 0
        batch.append(p)
        size += len(p)
        nlace += laces(p)
        g = min(granules[i], total)
    out.append(_page(serial, seq, total, batch, 0x04))
    return b"".join(out), total
