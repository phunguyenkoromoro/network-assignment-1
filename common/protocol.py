import json
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_SIZE = 1024 * 1024
NONCE_SIZE = 12
TAG_SIZE = 16
KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secret.key")


class PacketTooLarge(ValueError):
    pass


class DecryptError(ValueError):
    pass


def load_key():
    try:
        with open(KEY_FILE, "xb") as f:
            f.write(AESGCM.generate_key(bit_length=256))
    except FileExistsError:
        pass
    with open(KEY_FILE, "rb") as f:
        return f.read()


aes = AESGCM(load_key())


def recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Kết nối đã đóng")
        data += chunk
    return data


def send_packet(sock, packet):
    body = json.dumps(packet, ensure_ascii=False).encode("utf-8")
    nonce = os.urandom(NONCE_SIZE)
    data = nonce + aes.encrypt(nonce, body, None)
    sock.sendall(struct.pack(">I", len(data)) + data)


def recv_packet(sock):
    (size,) = struct.unpack(">I", recv_exact(sock, 4))
    if size > MAX_SIZE:
        raise PacketTooLarge(size)
    data = recv_exact(sock, size)
    if size < NONCE_SIZE + TAG_SIZE:
        raise DecryptError("Gói mã hóa quá ngắn")
    try:
        body = aes.decrypt(data[:NONCE_SIZE], data[NONCE_SIZE:], None)
    except InvalidTag:
        raise DecryptError("Giải mã thất bại: sai khóa hoặc dữ liệu bị sửa")
    return json.loads(body.decode("utf-8"))