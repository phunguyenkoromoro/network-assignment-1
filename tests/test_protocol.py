# test phần đóng gói + mã hóa trong common/protocol.py

import socket
import struct
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common import protocol


# gửi 1 gói qua socketpair rồi lấy đúng những byte chạy trên đường truyền
def wire_bytes(packet):
    a, b = socket.socketpair()
    with a, b:
        protocol.send_packet(a, packet)
        b.settimeout(1)
        return b.recv(65536)


class ProtocolTest(unittest.TestCase):
    def test_roundtrip_keeps_vietnamese_text(self):
        packet = {"type": "MSG", "text": "xin chào, đây là tiếng Việt"}
        a, b = socket.socketpair()
        with a, b:
            protocol.send_packet(a, packet)
            self.assertEqual(protocol.recv_packet(b), packet)

    def test_wire_does_not_leak_plaintext(self):
        raw = wire_bytes({"type": "AUTH", "username": "phu", "password": "matkhau-bi-mat"})
        for secret in (b"AUTH", b"phu", b"matkhau"):
            self.assertNotIn(secret, raw)

    def test_header_matches_length(self):
        raw = wire_bytes({"type": "PING"})
        self.assertEqual(struct.unpack(">I", raw[:4])[0], len(raw) - 4)

    # nonce ngẫu nhiên nên 2 gói giống hệt nhau vẫn ra byte khác nhau
    def test_same_packet_gives_different_bytes(self):
        self.assertNotEqual(wire_bytes({"type": "PING"}), wire_bytes({"type": "PING"}))

    def test_flipped_bit_is_rejected(self):
        raw = bytearray(wire_bytes({"type": "MSG", "text": "x"}))
        raw[20] ^= 1
        a, b = socket.socketpair()
        with a, b:
            a.sendall(bytes(raw))
            with self.assertRaises(protocol.DecryptError):
                protocol.recv_packet(b)

    def test_wrong_key_is_rejected(self):
        a, b = socket.socketpair()
        original = protocol.aes
        with a, b:
            protocol.send_packet(a, {"type": "PING"})
            protocol.aes = AESGCM(AESGCM.generate_key(bit_length=256))
            try:
                with self.assertRaises(protocol.DecryptError):
                    protocol.recv_packet(b)
            finally:
                protocol.aes = original

    def test_too_short_packet_is_rejected(self):
        a, b = socket.socketpair()
        with a, b:
            a.sendall(struct.pack(">I", 5) + b"abcde")
            with self.assertRaises(protocol.DecryptError):
                protocol.recv_packet(b)

    def test_too_large_header_is_rejected(self):
        a, b = socket.socketpair()
        with a, b:
            a.sendall(struct.pack(">I", protocol.MAX_SIZE + 1))
            with self.assertRaises(protocol.PacketTooLarge):
                protocol.recv_packet(b)

    def test_closed_connection_raises(self):
        a, b = socket.socketpair()
        a.close()
        with b:
            with self.assertRaises(ConnectionError):
                protocol.recv_packet(b)

    # TCP có thể trả dữ liệu từng đoạn nhỏ, recv_exact phải đọc cho đủ
    def test_recv_exact_reassembles_fragments(self):
        a, b = socket.socketpair()
        with a, b:
            a.sendall(b"ab")
            a.sendall(b"cde")
            self.assertEqual(protocol.recv_exact(b, 5), b"abcde")


if __name__ == "__main__":
    unittest.main()
