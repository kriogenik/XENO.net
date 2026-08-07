#!/usr/bin/env python3
"""Unit tests: hop_src, classify XHTTP, path_stats canary mask signal."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

from diag.classify import classify_error_line  # noqa: E402
from diag.hop_src import HOP_SRC_CANARY, HOP_SRC_RU, classify_hop_src  # noqa: E402
from diag.parse import parse_access_line  # noqa: E402
from diag.path_stats import compute_path_stats  # noqa: E402


def test_hop_src() -> None:
    os.environ["RU_PUBLIC_IP"] = "201.34.131.141"
    assert classify_hop_src("127.0.0.1") == HOP_SRC_CANARY
    assert classify_hop_src("201.34.131.141") == HOP_SRC_RU
    assert classify_hop_src("8.8.8.8") == "other"


def test_classify_xhttp() -> None:
    assert classify_error_line("proxy/vless: firstLen = 0") == "xhttp_eof"
    assert (
        classify_error_line("unexpected response version. Expecting 0 but actually 88")
        == "xhttp_version"
    )


def test_parse_src_ip() -> None:
    line = (
        "2026/08/07 12:00:00.000000 from 127.0.0.1:12345 accepted tcp:1.1.1.1:53 "
        "email: xeno-relay-hop [xeno-relay-in -> direct]"
    )
    ev = parse_access_line(line)
    assert ev and ev.hop and ev.src_ip == "127.0.0.1"


def test_path_stats_canary_mask() -> None:
    now = int(time.time())
    ts = time.strftime("%Y/%m/%d %H:%M:%S", time.gmtime(now - 60))
    ru_lines = [
        f"{ts} from 1.2.3.4:1 accepted tcp:x:443 email: tg-1 [client-in -> nl-exit]",
        f"{ts} from 1.2.3.4:2 accepted tcp:x:443 email: tg-2 [client-in -> nl-exit]",
        f"{ts} from 1.2.3.4:3 accepted tcp:x:443 email: tg-3 [client-in -> nl-exit]",
        f"{ts} from 1.2.3.4:4 accepted tcp:x:443 email: tg-4 [client-in -> nl-exit]",
        f"{ts} from 1.2.3.4:5 accepted tcp:x:443 email: tg-5 [client-in -> nl-exit]",
    ]
    # pad to >=10 accepts for cascade_ratio_break
    for i in range(10):
        ru_lines.append(
            f"{ts} from 1.2.3.4:{10+i} accepted tcp:x:443 email: tg-{i} [client-in -> nl-exit]"
        )
    nl_lines = [
        f"{ts} from 127.0.0.1:9 accepted tcp:x:443 email: xeno-relay-hop [xeno-relay-in -> direct]",
    ]
    os.environ["RU_PUBLIC_IP"] = "201.34.131.141"
    st = compute_path_stats(ru_lines=ru_lines, nl_lines=nl_lines, ru_ip="201.34.131.141", now=now)
    assert st["signals"]["canary_mask_risk"] or st["signals"]["cascade_ratio_break"]
    assert st["windows"]["15m"]["hop_canary"] >= 1
    assert st["windows"]["15m"]["hop_ru_sourced"] == 0


def main() -> int:
    test_hop_src()
    test_classify_xhttp()
    test_parse_src_ip()
    test_path_stats_canary_mask()
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
