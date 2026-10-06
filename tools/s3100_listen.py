"""Passive listener for reverse engineering S3100 settings.

Connects to the bridge and only listens: it never logs in, never ACKs and
never sends a single byte, so it does not disturb the session of Home
Assistant (the bridge forwards the controller's output to every client).
Change a setting on the boiler display and watch which frames arrive.

    python tools/s3100_listen.py 192.168.178.60
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from froeling_s3100.protocol import FrameParser, bcd, decode_text

QUIET = {"M1", "M2"}  # Sent every second; only shown when they change.


def describe(command: str, p: bytes) -> str:
    if command == "MI" and len(p) == 4:
        return f"PARAMETER {int.from_bytes(p[:2], 'big')} -> raw {int.from_bytes(p[2:], 'big', signed=True)}"
    if command == "MG" and len(p) == 18:
        return (
            f"WEEKLY PROGRAM circuit {int.from_bytes(p[:2], 'big')}: "
            f"b2={p[2]} block1={list(p[3:10])} b10={p[10]} block2={list(p[11:18])}"
        )
    if command in ("M3", "MU") and len(p) == 10:
        when = f"20{bcd(p[9]):02d}-{p[7]:02x}-{p[6]:02x} {p[5]:02x}:{p[4]:02x}"
        return f"FAULT id {p[0]} flags {p[1]:#04x} state {p[2]} at {when}"
    if command.startswith("R"):
        return f"response/other client {p.hex()}"
    return f"{p.hex()} {decode_text(p)[:40]}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=4196)
    parser.add_argument("--out", type=Path, default=Path("captures"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    log_path = args.out / f"listen-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"

    sock = socket.create_connection((args.host, args.port), timeout=5)
    sock.settimeout(1.0)
    frames = FrameParser()
    last: dict[str, bytes] = {}
    print(f"Listening (sending nothing). Log: {log_path}. Ctrl+C to stop.")
    with log_path.open("w", encoding="utf-8") as log:
        try:
            while True:
                try:
                    data = sock.recv(4096)
                except TimeoutError:
                    continue
                if not data:
                    print("Bridge closed the connection")
                    break
                for frame in frames.feed(data):
                    stamp = time.strftime("%H:%M:%S")
                    log.write(
                        json.dumps({"t": stamp, "cmd": frame.command, "payload": frame.payload.hex()}) + "\n"
                    )
                    log.flush()
                    if frame.payload == b"\x01" and frame.command[0] == "M":
                        continue  # ACK from Home Assistant
                    if frame.command in QUIET:
                        if last.get(frame.command) is not None and frame.command == "M1":
                            old, new = last["M1"], frame.payload
                            changed = [
                                i // 2 for i in range(0, len(new), 2) if old[i : i + 2] != new[i : i + 2]
                            ]
                            if changed:
                                print(f"{stamp} M1 values changed at positions {changed}")
                        last[frame.command] = frame.payload
                        continue
                    print(f"{stamp} {frame.command} {describe(frame.command, frame.payload)}")
        except KeyboardInterrupt:
            pass
    sock.close()


if __name__ == "__main__":
    main()
