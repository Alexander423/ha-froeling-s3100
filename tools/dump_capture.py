"""Print every received frame of a capture JSONL file in readable form."""

import json
import sys

path = sys.argv[1]
for line in open(path, encoding="utf-8"):
    r = json.loads(line)
    if r.get("dir") != "rx" or "payload" not in r or r.get("note") == "ack":
        continue
    p = bytes.fromhex(r["payload"])
    txt = "".join(chr(b) if 32 <= b < 127 else "." for b in p)
    cp = p.decode("cp850", errors="replace").replace("\n", "|")
    print(f"{r['cmd']} {len(p):3d} {p.hex(' ')}  |{cp}|")
