#!/usr/bin/env python3
import json
import struct
import sys
import time


def read_exact(f, n):
    buf = b""
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def write(out, header, blob=None):
    if blob is not None:
        header["blob"] = len(blob)
    body = json.dumps(header).encode()
    out.write(struct.pack(">I", len(body)) + body + (blob or b""))
    out.flush()


def main():
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    sys.stderr.write("fake-hyprhands serve: ready\n")
    sys.stderr.flush()
    while True:
        head = read_exact(inp, 4)
        if head is None:
            return 0
        body = read_exact(inp, struct.unpack(">I", head)[0])
        if body is None:
            return 1
        req = json.loads(body)
        op = req.get("op")
        if op == "fail":
            write(out, {"ok": False, "error": "fake failure"})
        elif op == "capture":
            n = int(req.get("size", 16))
            write(out, {"ok": True, "op": op, "w": 4, "h": 1}, bytes(i % 256 for i in range(n)))
        elif op == "sleep":
            time.sleep(int(req.get("ms", 50)) / 1000)
            write(out, {"ok": True, "op": op})
        elif op == "exit":
            return 3
        elif op == "garbage":
            out.write(struct.pack(">I", 5) + b"notjs")
            out.flush()
        else:
            write(out, {"ok": True, "op": op, "echo": req})


if __name__ == "__main__":
    sys.exit(main())
