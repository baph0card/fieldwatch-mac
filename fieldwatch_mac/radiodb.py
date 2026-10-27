"""Reader for radiodb.bin: IEEE OUI/MA-M/MA-S/CID names and Bluetooth SIG company, service and appearance names.

The format is the one written by Fieldwatch's scripts/build_lookups.py:
  header  "SPLK", u16 version, u16 0, u32 built, u32 nsections
  table   nsections x (4-byte tag, u32 absolute offset)
  pairs   u32 count, then keys (u32 | u16 | u64), then u16 name indexes
  names   "noff": u32 count, (count+1) x u32 offsets; "nstr": the UTF-8 blob
"""

from __future__ import annotations

import bisect
import struct
from pathlib import Path


class _Pairs:
    def __init__(self, buf: memoryview, key_fmt: str):
        self._ksz = struct.calcsize(key_fmt)
        (self.n,) = struct.unpack_from("<I", buf, 0)
        keys_end = 4 + self.n * self._ksz
        self.keys = list(struct.unpack_from(f"<{self.n}{key_fmt}", buf, 4)) if self.n else []
        self.idx = list(struct.unpack_from(f"<{self.n}H", buf, keys_end)) if self.n else []

    def get(self, key: int) -> int | None:
        i = bisect.bisect_left(self.keys, key)
        if i < self.n and self.keys[i] == key:
            return self.idx[i]
        return None


class RadioDb:
    def __init__(self, path: Path):
        raw = Path(path).read_bytes()
        if raw[:4] != b"SPLK":
            raise ValueError("radiodb.bin: bad magic")
        buf = memoryview(raw)
        _ver, _pad, self.built, nsec = struct.unpack_from("<HHII", buf, 4)
        table = []
        for i in range(nsec):
            tag, off = struct.unpack_from("<4sI", buf, 16 + 8 * i)
            table.append((tag.rstrip(b"\x00").decode("ascii"), off))
        ends = [off for _, off in table[1:]] + [len(raw)]
        sect = {tag: buf[off:end] for (tag, off), end in zip(table, ends)}

        self.mal = _Pairs(sect["mal"], "I")
        self.cid = _Pairs(sect["cid"], "I")
        self.long = _Pairs(sect["long"], "Q")
        self.btc = _Pairs(sect["btc"], "H")
        self.app = _Pairs(sect["app"], "H")
        self.uuid = _Pairs(sect["uuid"], "H")

        noff = sect["noff"]
        (n,) = struct.unpack_from("<I", noff, 0)
        self._offs = struct.unpack_from(f"<{n + 1}I", noff, 4)
        self._blob = bytes(sect["nstr"])

    def _name(self, idx: int | None) -> str | None:
        if idx is None:
            return None
        return self._blob[self._offs[idx] : self._offs[idx + 1]].decode("utf-8", "replace")

    def vendor_for_mac(self, mac_hex: str) -> str | None:
        """mac_hex: 6+ hex digits, no separators. Longest registry block wins (MA-S, MA-M, then MA-L)."""
        h = mac_hex.upper()
        if len(h) < 6:
            return None
        try:
            if len(h) >= 9:
                hit = self.long.get((36 << 56) | int(h[:9], 16))
                if hit is not None:
                    return self._name(hit)
            if len(h) >= 7:
                hit = self.long.get((28 << 56) | int(h[:7], 16))
                if hit is not None:
                    return self._name(hit)
            v = int(h[:6], 16)
        except ValueError:
            return None
        hit = self.mal.get(v)
        if hit is None:
            hit = self.cid.get(v)
        return self._name(hit)

    def company(self, company_id: int) -> str | None:
        return self._name(self.btc.get(company_id))

    def service(self, uuid16: int) -> str | None:
        return self._name(self.uuid.get(uuid16))

    def appearance(self, value: int) -> str | None:
        return self._name(self.app.get(value))
