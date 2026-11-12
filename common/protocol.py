import json
import struct

MAX_SIZE = 1024 * 1024


def recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Kết nối đã bị đóng")
        data += chunk
    return data


def send_packet(sock, packet):
    body = json.dumps(packet, ensure_ascii=False).encode("utf-8")
    sock.sendall(struct.pack(">I", len(body)) + body)


def recv_packet(sock):
    (size,) = struct.unpack(">I", recv_exact(sock, 4))
    if size > MAX_SIZE:
        raise ValueError("PACKET_TOO_LARGE")
    return json.loads(recv_exact(sock, size).decode("utf-8"))
