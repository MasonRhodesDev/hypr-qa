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
    if blob is not None:
        header = dict(header, blob=len(blob))
    return encode(header) + (blob or b"")


def read_exact(fd, n, deadline):
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
    try:
        os.killpg(p.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


class Serve:
    def __init__(self, argv, stderr_path=None, env=None, timeout=30.0):
        self.argv, self.timeout = list(argv), timeout
        self._err = open(stderr_path, "ab") if stderr_path else subprocess.DEVNULL
        self.p = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self._err, env=env, bufsize=0, start_new_session=True)
        self.dead = None
        self.killed = False

    def request(self, req, timeout=None):
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
            killpg(self.p, signal.SIGKILL)
            if rc is None:
                rc = self.p.wait()
        if self._err is not subprocess.DEVNULL:
            self._err.close()
        return rc
