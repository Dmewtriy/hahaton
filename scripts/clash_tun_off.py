"""Temporarily disable Clash Verge TUN via mihomo named pipe."""
import ctypes
import json
from ctypes import byref, create_string_buffer, c_ulong


def pipe_request(method: str, path: str, body: bytes | None = None) -> bytes:
    generic_read = 0x80000000
    generic_write = 0x40000000
    open_existing = 3
    k = ctypes.windll.kernel32
    handle = k.CreateFileW(
        r"\\.\pipe\verge-mihomo",
        generic_read | generic_write,
        0,
        None,
        open_existing,
        0,
        None,
    )
    if handle in (-1, 0xFFFFFFFF):
        raise SystemExit(f"pipe open failed: {k.GetLastError()}")
    mode = c_ulong(2)
    k.SetNamedPipeHandleState(handle, byref(mode), None, None)
    payload = b"" if body is None else body
    headers = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
    if body is not None:
        headers += f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
    req = (headers + "\r\n").encode() + payload
    written = c_ulong(0)
    k.WriteFile(handle, req, len(req), byref(written), None)
    data = b""
    while True:
        buf = create_string_buffer(8192)
        read = c_ulong(0)
        ok = k.ReadFile(handle, buf, 8192, byref(read), None)
        if not ok or read.value == 0:
            break
        data += buf.raw[:read.value]
        if b"\r\n\r\n" in data:
            head, _, rest = data.partition(b"\r\n\r\n")
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    need = int(line.split(b":")[1])
                    while len(rest) < need:
                        buf = create_string_buffer(8192)
                        read = c_ulong(0)
                        if not k.ReadFile(handle, buf, 8192, byref(read), None) or read.value == 0:
                            break
                        rest += buf.raw[:read.value]
                    data = head + b"\r\n\r\n" + rest
            break
    k.CloseHandle(handle)
    return data


def main() -> None:
    patch = pipe_request("PATCH", "/configs", json.dumps({"tun": {"enable": False}}).encode())
    print(patch.split(b"\r\n", 1)[0].decode())
    cfg = pipe_request("GET", "/configs")
    body = cfg.split(b"\r\n\r\n", 1)[1]
    data = json.loads(body)
    print("tun.enable=", data.get("tun", {}).get("enable"), "mode=", data.get("mode"))


if __name__ == "__main__":
    main()
