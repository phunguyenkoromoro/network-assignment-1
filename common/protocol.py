# file này lo việc đóng gói + mã hóa gói tin, server với client xài chung
# 1 gói tin gồm: 4 byte độ dài | 12 byte nonce | ciphertext + 16 byte tag
# (độ dài N là độ dài của nonce + ciphertext + tag, nội dung gốc là JSON rồi mới mã hóa AES-GCM)

import json
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# gói nào lớn hơn 1 MB thì từ chối luôn
MAX_SIZE = 1024 * 1024
NONCE_SIZE = 12
TAG_SIZE = 16
# khóa AES chung nằm ở secret.key, server với client phải xài CÙNG 1 khóa
KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secret.key")


# header báo độ dài lớn hơn MAX_SIZE
class PacketTooLarge(ValueError):
    pass


# giải mã lỗi: sai khóa, gói bị sửa, hoặc gói quá ngắn
class DecryptError(ValueError):
    pass


# đọc khóa từ secret.key, chưa có thì tự sinh khóa 256 bit
def load_key():
    try:
        # mode "x" là chỉ tạo khi file CHƯA có, để server với client chạy cùng lúc không ghi đè khóa
        # của nhau
        with open(KEY_FILE, "xb") as f:
            f.write(AESGCM.generate_key(bit_length=256))
    except FileExistsError:
        pass
    with open(KEY_FILE, "rb") as f:
        return f.read()


aes = AESGCM(load_key())


# TCP là dòng byte liên tục, recv có thể trả thiếu nên phải đọc lặp tới khi đủ n byte
def recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Kết nối đã đóng")
        data += chunk
    return data


# dict -> JSON -> mã hóa -> gửi kèm 4 byte độ dài
# NHỚ mỗi gói dùng 1 nonce ngẫu nhiên riêng, không được xài lại
def send_packet(sock, packet):
    body = json.dumps(packet, ensure_ascii=False).encode("utf-8")
    nonce = os.urandom(NONCE_SIZE)
    data = nonce + aes.encrypt(nonce, body, None)
    sock.sendall(struct.pack(">I", len(data)) + data)


# ngược lại với send_packet: đọc 4 byte độ dài trước, rồi đọc đủ N byte, giải mã, rồi mới parse JSON
def recv_packet(sock):
    (size,) = struct.unpack(">I", recv_exact(sock, 4))
    if size > MAX_SIZE:
        raise PacketTooLarge(size)
    data = recv_exact(sock, size)
    if size < NONCE_SIZE + TAG_SIZE:
        raise DecryptError("Gói mã hóa quá ngắn")
    try:
        body = aes.decrypt(data[:NONCE_SIZE], data[NONCE_SIZE:], None)
    # tag không khớp nghĩa là sai khóa hoặc có người sửa gói
    except InvalidTag:
        raise DecryptError("Giải mã thất bại: sai khóa hoặc dữ liệu bị sửa")
    return json.loads(body.decode("utf-8"))