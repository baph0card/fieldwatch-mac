"""Parse raw 802.11 information elements (CoreWLAN's CWNetwork.informationElementData)."""

from __future__ import annotations

from dataclasses import dataclass, field

_AKM = {
    1: "802.1X",
    2: "PSK",
    3: "FT-802.1X",
    4: "FT-PSK",
    5: "802.1X-SHA256",
    6: "PSK-SHA256",
    8: "SAE",
    9: "FT-SAE",
    11: "SuiteB",
    12: "SuiteB-192",
    18: "OWE",
}


@dataclass
class IeInfo:
    ssid_bytes: bytes | None = None
    channel: int | None = None
    country: str = ""
    security: str = ""
    standards: str = ""
    vendor_ouis: list[str] = field(default_factory=list)  # "AA:BB:CC", unique, in order of appearance
    has_rsn: bool = False
    has_wpa: bool = False
    wps: bool = False


def _iter_ies(data: bytes):
    i, n = 0, len(data)
    while i + 2 <= n:
        tag, ln = data[i], data[i + 1]
        if i + 2 + ln > n:
            return
        yield tag, data[i + 2 : i + 2 + ln]
        i += 2 + ln


def _rsn_akms(body: bytes) -> set[str]:
    # version(2) group(4) pairwise_count(2) pairwise(4*n) akm_count(2) akm(4*n)
    try:
        p = 2 + 4
        pc = int.from_bytes(body[p : p + 2], "little")
        p += 2 + 4 * pc
        ac = int.from_bytes(body[p : p + 2], "little")
        p += 2
        out = set()
        for _ in range(ac):
            suite = body[p : p + 4]
            p += 4
            if len(suite) == 4 and suite[:3] == b"\x00\x0f\xac":
                out.add(_AKM.get(suite[3], f"akm{suite[3]}"))
        return out
    except Exception:
        return set()


def _security_label(akms: set[str], has_wpa: bool) -> str:
    psk = bool({"PSK", "FT-PSK", "PSK-SHA256"} & akms)
    sae = bool({"SAE", "FT-SAE"} & akms)
    ent = bool({"802.1X", "FT-802.1X", "802.1X-SHA256", "SuiteB", "SuiteB-192"} & akms)
    if "OWE" in akms:
        return "OWE (Enhanced Open)"
    if sae and psk:
        return "WPA2/WPA3"
    if sae:
        return "WPA3"
    if ent:
        return "WPA2/3 Enterprise"
    if psk:
        return "WPA/WPA2" if has_wpa else "WPA2"
    return "RSN"


def parse(data: bytes | None) -> IeInfo:
    info = IeInfo()
    if not data:
        return info
    std = []
    akms: set[str] = set()
    seen = set()
    for tag, body in _iter_ies(data):
        if tag == 0 and info.ssid_bytes is None:
            info.ssid_bytes = bytes(body)
        elif tag == 3 and body:
            info.channel = body[0]
        elif tag == 7 and len(body) >= 2:
            info.country = body[:2].decode("ascii", "replace")
        elif tag == 48:
            info.has_rsn = True
            akms |= _rsn_akms(body)
        elif tag == 45:
            std.append("n")
        elif tag == 191:
            std.append("ac")
        elif tag == 255 and body:
            ext = body[0]
            if ext == 35:
                std.append("ax")
            elif ext == 106:
                std.append("be")
        elif tag == 221 and len(body) >= 3:
            oui = body[:3]
            if oui == b"\x00\x50\xf2" and len(body) >= 4:
                if body[3] == 1:
                    info.has_wpa = True
                elif body[3] == 4:
                    info.wps = True
            h = ":".join("%02X" % b for b in oui)
            if h not in seen:
                seen.add(h)
                info.vendor_ouis.append(h)
    if info.has_rsn:
        info.security = _security_label(akms, info.has_wpa)
    elif info.has_wpa:
        info.security = "WPA"
    else:
        info.security = "Open/WEP"
    order = ["n", "ac", "ax", "be"]
    top = [s for s in order if s in std]
    if top:
        info.standards = {"n": "Wi-Fi 4", "ac": "Wi-Fi 5", "ax": "Wi-Fi 6", "be": "Wi-Fi 7"}[top[-1]]
    return info
