"""The hyprhands serve wire protocol (hyprhands src/proto.rs) and a stamped client.

Request: u32 big-endian length, then that many bytes of JSON {"op": ..., ...args}.
Reply:   u32 big-endian length, then JSON {"ok": true, ...} or {"ok": false, "error": ...};
         when the JSON has "blob": n, exactly n raw bytes follow it.

The client starts ONE long-lived serve process (any argv: `vm session hyprhands
serve ...` in a run, the fake stub in tests) and stamps every request on the
host's CLOCK_MONOTONIC: t_send just before the request is written, t_ack once
the whole reply (header and blob) has been read.
"""
import json
import os
import select
import signal
import struct
import subprocess
import time

MAX_REPLY = 256 << 20


class ProtoError(Exception):
    pass


def encode(obj):
    body = json.dumps(obj, separators=(",", ":")).encode()
    return struct.pack(">I", len(body)) + body


def encode_reply(header, blob=None):
    """What serve writes (used by the fake serve and tests)."""
    if blob is not None:
        header = dict(header, blob=len(blob))
    return encode(header) + (blob or b"")


def read_exact(fd, n, deadline):
    """n bytes from fd before the monotonic deadline (seconds), or ProtoError."""
    buf = bytearray()
    while len(buf) < n:
        left = deadline - time.monotonic()
        if left <= 0:
            raise ProtoError(f"timed out waiting for the reply ({len(buf)} of {n} bytes)")
        r, _, _ = select.select([fd], [], [], left)
        if not r:
            continue
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            raise ProtoError("serve closed its output (exited?)" + (f" after {len(buf)} of {n} bytes" if buf else ""))
        buf += chunk
    return bytes(buf)


def read_message(fd, deadline):
    """(header dict, blob bytes or None) from fd."""
    (n,) = struct.unpack(">I", read_exact(fd, 4, deadline))
    if n > MAX_REPLY:
        raise ProtoError(f"reply header of {n} bytes is over the limit")
    try:
        header = json.loads(read_exact(fd, n, deadline))
    except ValueError as e:
        raise ProtoError(f"reply is not JSON: {e}") from None
    if not isinstance(header, dict):
        raise ProtoError(f"reply is not a JSON object: {header!r}")
    blob = None
    if "blob" in header:
        size = header["blob"]
        if not isinstance(size, int) or size < 0 or size > MAX_REPLY:
            raise ProtoError(f"bad blob size {size!r}")
        blob = read_exact(fd, size, deadline)
    return header, blob


def killpg(p, sig):
    """Signal p's process group (p was started with start_new_session=True)."""
    try:
        os.killpg(p.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


class Serve:
    """One long-lived serve process. request() is synchronous: one in flight."""

    def __init__(self, argv, stderr_path=None, env=None, timeout=30.0):
        self.argv, self.timeout = list(argv), timeout
        self._err = open(stderr_path, "ab") if stderr_path else subprocess.DEVNULL
        # Own process group: a kill reaches vm -> vmkit -> ssh, not just the wrapper.
        self.p = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self._err, env=env, bufsize=0, start_new_session=True)
        self.dead = None
        self.killed = False   # close() had to kill it (the far end may still be running)

    def request(self, req, timeout=None):
        """Send req; returns a record {t_send, t_ack, ok, reply, blob, error?}. Never raises
        for protocol or serve failures: they come back as ok=False with the error."""
        if self.dead:
            t = time.monotonic_ns()
            return {"t_send": t, "t_ack": t, "ok": False, "error": self.dead, "reply": None, "blob": None}
        data = encode(req)
        deadline = time.monotonic() + (timeout or self.timeout)
        t_send = time.monotonic_ns()
        try:
            self.p.stdin.write(data)
            self.p.stdin.flush()
            header, blob = read_message(self.p.stdout.fileno(), deadline)
        except (OSError, ProtoError, struct.error) as e:
            t_ack = time.monotonic_ns()
            self.dead = f"hyprhands serve: {e}"
            rc = self.p.poll()
            if rc is not None:
                self.dead += f" (exit {rc})"
            return {"t_send": t_send, "t_ack": t_ack, "ok": False, "error": self.dead, "reply": None, "blob": None}
        t_ack = time.monotonic_ns()
        ok = header.get("ok") is True
        rec = {"t_send": t_send, "t_ack": t_ack, "ok": ok, "reply": header, "blob": blob}
        if not ok:
            rec["error"] = str(header.get("error") or "serve replied ok=false")
        return rec

    def close(self, timeout=10.0):
        """EOF on stdin (serve exits cleanly at end of input), then wait; kill if it hangs."""
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            rc = self.p.wait(timeout)
        except subprocess.TimeoutExpired:
            self.killed = True
            killpg(self.p, signal.SIGTERM)
            try:
                rc = self.p.wait(5)
            except subprocess.TimeoutExpired:
                rc = None
            killpg(self.p, signal.SIGKILL)   # the leader may be gone while its children linger
            if rc is None:
                rc = self.p.wait()
        if self._err is not subprocess.DEVNULL:
            self._err.close()
        return rc
