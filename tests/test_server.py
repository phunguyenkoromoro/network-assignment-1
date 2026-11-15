# test server: chạy server thật (cổng ngẫu nhiên, file tài khoản tạm) rồi kết nối bằng socket

import base64
import hashlib
import itertools
import os
import socket
import struct
import tempfile
import threading
import time
import unittest

import server.server as srv
from common import protocol

PORT = None
_counter = itertools.count(1)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# chạy server 1 lần cho cả file test, dùng file tài khoản tạm để không đụng users.json thật
def setUpModule():
    global PORT
    PORT = free_port()
    srv.PORT = PORT
    srv.USERS_FILE = os.path.join(tempfile.mkdtemp(), "users.json")
    srv.accounts.clear()
    srv.PING_INTERVAL = 0.5
    threading.Thread(target=srv.main, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Server không khởi động được")


def unique(prefix="u"):
    return f"{prefix}{next(_counter)}"


# client thô để test: gửi / nhận gói tin, tự bỏ qua PING của server
class Client:

    def __init__(self, name=None, password="pw", login=True):
        self.name = name if name is not None else unique()
        self.sock = socket.create_connection(("127.0.0.1", PORT), timeout=5)
        self.login_reply = None
        if login:
            self.send({"type": "AUTH", "username": self.name, "password": password})
            self.login_reply = self.recv_type("AUTH_OK", "AUTH_FAIL")

    def send(self, packet):
        protocol.send_packet(self.sock, packet)

    # NHỚ: đồng hồ chờ tính cho cả vòng lặp, không để PING làm reset, không là test treo luôn
    def recv(self, timeout=2):
        end = time.time() + timeout
        while True:
            remaining = end - time.time()
            if remaining <= 0:
                raise socket.timeout("hết thời gian chờ")
            self.sock.settimeout(remaining)
            packet = protocol.recv_packet(self.sock)
            if packet.get("type") != "PING":
                return packet

    def recv_type(self, *kinds, timeout=2):
        end = time.time() + timeout
        while True:
            packet = self.recv(end - time.time())
            if packet.get("type") in kinds:
                return packet

    def expect_nothing(self, *kinds, timeout=0.6):
        end = time.time() + timeout
        while True:
            try:
                packet = self.recv(end - time.time())
            except socket.timeout:
                return
            if packet.get("type") in kinds:
                raise AssertionError(f"Không mong đợi nhận {packet}")

    def is_closed(self, timeout=2):
        end = time.time() + timeout
        while True:
            try:
                self.recv(end - time.time())
            except socket.timeout:
                return False
            except OSError:
                return True

    def close(self):
        self.sock.close()


class ServerTestCase(unittest.TestCase):
    def client(self, *args, **kwargs):
        c = Client(*args, **kwargs)
        self.addCleanup(c.close)
        return c


class AuthTest(ServerTestCase):
    def test_register_then_login_again(self):
        name = unique()
        first = self.client(name, "secret")
        self.assertEqual(first.login_reply["type"], "AUTH_OK")
        first.send({"type": "BYE"})
        first.close()
        time.sleep(0.3)
        again = self.client(name, "secret")
        self.assertEqual(again.login_reply["type"], "AUTH_OK")

    def test_wrong_password(self):
        name = unique()
        first = self.client(name, "secret")
        first.send({"type": "BYE"})
        time.sleep(0.3)
        second = self.client(name, "sai")
        self.assertEqual(second.login_reply, {"type": "AUTH_FAIL", "reason": "Sai mật khẩu"})

    def test_duplicate_online_name(self):
        name = unique()
        self.client(name)
        second = self.client(name)
        self.assertEqual(second.login_reply["reason"], "Tên đã được dùng")

    def test_invalid_names_and_empty_password(self):
        for bad in ("a b", "ALL", "all", "x" * 21, ""):
            c = self.client(bad)
            self.assertEqual(c.login_reply["reason"], "Tên không hợp lệ", bad)
        c = self.client(unique(), "")
        self.assertEqual(c.login_reply["reason"], "Mật khẩu không được để trống")

    def test_must_login_first(self):
        c = self.client(login=False)
        c.send({"type": "MSG", "to": "ALL", "text": "hi"})
        reply = c.recv()
        self.assertEqual(reply["type"], "ERROR")
        self.assertEqual(reply["code"], "NOT_AUTHENTICATED")

    def test_non_string_credentials_are_bad_packet(self):
        c = self.client(login=False)
        c.send({"type": "AUTH", "username": 123, "password": "x"})
        self.assertEqual(c.recv()["code"], "BAD_PACKET")

    def test_users_broadcast_on_join_and_leave(self):
        a = self.client()
        b = self.client()
        users = a.recv_type("USERS")["users"]
        while b.name not in users:
            users = a.recv_type("USERS")["users"]
        self.assertIn(a.name, users)
        b.close()
        users = a.recv_type("USERS")["users"]
        while b.name in users:
            users = a.recv_type("USERS")["users"]
        self.assertNotIn(b.name, users)

    def test_get_users(self):
        a = self.client()
        a.send({"type": "GET_USERS"})
        self.assertIn(a.name, a.recv_type("USERS")["users"])

    def test_server_tracks_ip_and_port(self):
        a = self.client()
        with srv.lock:
            host, port = srv.clients[a.name].addr
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, a.sock.getsockname()[1])


class MessagingTest(ServerTestCase):
    def test_broadcast_reaches_others_but_not_sender(self):
        a, b, c = self.client(), self.client(), self.client()
        a.send({"type": "MSG", "to": "ALL", "text": "chào cả nhà"})
        for other in (b, c):
            msg = other.recv_type("MSG")
            self.assertEqual((msg["from"], msg["to"], msg["text"]), (a.name, "ALL", "chào cả nhà"))
        a.expect_nothing("MSG")

    def test_private_message_only_reaches_target(self):
        a, b, c = self.client(), self.client(), self.client()
        a.send({"type": "MSG", "to": b.name, "text": "riêng"})
        self.assertEqual(b.recv_type("MSG")["text"], "riêng")
        c.expect_nothing("MSG")
        a.expect_nothing("MSG")

    def test_offline_target(self):
        a = self.client()
        a.send({"type": "MSG", "to": "khongco", "text": "alo"})
        self.assertEqual(a.recv_type("ERROR")["code"], "USER_OFFLINE")

    def test_sender_cannot_be_spoofed(self):
        a, b = self.client(), self.client()
        a.send({"type": "MSG", "from": "nguoikhac", "to": b.name, "text": "x"})
        self.assertEqual(b.recv_type("MSG")["from"], a.name)

    def test_bad_msg_fields(self):
        a = self.client()
        for packet in (
            {"type": "MSG", "to": "ALL", "text": ""},
            {"type": "MSG", "text": "thiếu to"},
            {"type": "MSG", "to": [1], "text": "to sai kiểu"},
            {"type": "MSG", "to": "ALL", "text": 5},
        ):
            a.send(packet)
            self.assertEqual(a.recv_type("ERROR")["code"], "BAD_PACKET", packet)
            a.send({"type": "PING"})
            a.recv_type("PONG")
            a.sock.settimeout(2)


class FileTransferTest(ServerTestCase):
    def start_packet(self, sender, to, file_id="f1", size=10):
        return {
            "type": "FILE_START",
            "from": sender.name,
            "to": to,
            "file_id": file_id,
            "filename": "x.bin",
            "size": size,
            "chunk_size": 4096,
        }

    def test_file_is_forwarded_intact(self):
        a, b = self.client(), self.client()
        data = os.urandom(50_000)
        chunks = [data[i : i + 4096] for i in range(0, len(data), 4096)]
        a.send(self.start_packet(a, b.name, "f1", len(data)))
        for seq, chunk in enumerate(chunks):
            a.send(
                {
                    "type": "FILE_CHUNK",
                    "file_id": "f1",
                    "seq": seq,
                    "data": base64.b64encode(chunk).decode(),
                }
            )
        a.send({"type": "FILE_END", "file_id": "f1", "sha256": hashlib.sha256(data).hexdigest()})
        start = b.recv_type("FILE_START")
        self.assertEqual(
            (start["from"], start["filename"], start["size"]), (a.name, "x.bin", len(data))
        )
        received = b""
        for seq in range(len(chunks)):
            packet = b.recv_type("FILE_CHUNK")
            self.assertEqual(packet["seq"], seq)
            received += base64.b64decode(packet["data"])
        end = b.recv_type("FILE_END")
        self.assertEqual(received, data)
        self.assertEqual(end["sha256"], hashlib.sha256(data).hexdigest())

    def test_offline_recipient(self):
        a = self.client()
        a.send(self.start_packet(a, "khongco"))
        self.assertEqual(a.recv_type("ERROR")["code"], "USER_OFFLINE")

    def test_all_is_not_a_valid_recipient(self):
        a = self.client()
        a.send(self.start_packet(a, "ALL"))
        self.assertEqual(a.recv_type("ERROR")["code"], "USER_OFFLINE")

    def test_file_too_large(self):
        a, b = self.client(), self.client()
        a.send(self.start_packet(a, b.name, "big", srv.MAX_FILE_SIZE + 1))
        reply = a.recv_type("ERROR")
        self.assertEqual((reply["code"], reply["file_id"]), ("FILE_ERROR", "big"))
        b.expect_nothing("FILE_START")

    def test_invalid_start_fields(self):
        a, b = self.client(), self.client()
        for bad in (
            {"filename": 123},
            {"size": -5},
            {"size": "10"},
            {"file_id": ""},
        ):
            packet = self.start_packet(a, b.name)
            packet.update(bad)
            a.send(packet)
            self.assertEqual(a.recv_type("ERROR")["code"], "BAD_PACKET", bad)

    def test_sender_disconnects_midway(self):
        a, b = self.client(), self.client()
        a.send(self.start_packet(a, b.name, "mid", 100_000))
        b.recv_type("FILE_START")
        a.close()
        error = b.recv_type("ERROR")
        self.assertEqual((error["code"], error["file_id"]), ("FILE_ERROR", "mid"))

    def test_recipient_disconnects_midway(self):
        a, b = self.client(), self.client()
        a.send(self.start_packet(a, b.name, "gone", 100_000))
        b.recv_type("FILE_START")
        b.close()
        time.sleep(0.5)
        a.send({"type": "FILE_CHUNK", "file_id": "gone", "seq": 0, "data": "AAAA"})
        error = a.recv_type("ERROR")
        self.assertEqual((error["code"], error["file_id"]), ("FILE_ERROR", "gone"))

    def test_other_user_cannot_hijack_a_transfer(self):
        a, b, c = self.client(), self.client(), self.client()
        a.send(self.start_packet(a, b.name, "mine", 3))
        b.recv_type("FILE_START")
        c.send({"type": "FILE_CHUNK", "file_id": "mine", "seq": 0, "data": "WFhY"})
        b.expect_nothing("FILE_CHUNK")


class RobustnessTest(ServerTestCase):
    def send_raw(self, c, body):
        c.sock.sendall(struct.pack(">I", len(body)) + body)

    def test_garbage_body_is_bad_packet_but_connection_stays(self):
        a = self.client()
        self.send_raw(a, os.urandom(60))
        self.assertEqual(a.recv_type("ERROR")["code"], "BAD_PACKET")
        a.send({"type": "PING"})
        a.recv_type("PONG")

    def test_wrong_json_shapes(self):
        a = self.client()
        for body in ([1, 2], {"text": "thiếu type"}, {"type": 5}, {"type": "KHONG_TON_TAI"}):
            a.send(body)
            self.assertEqual(a.recv_type("ERROR")["code"], "BAD_PACKET", body)
            a.sock.settimeout(2)

    def test_too_many_bad_packets_disconnects(self):
        a = self.client()
        for _ in range(srv.MAX_BAD_PACKETS):
            self.send_raw(a, b"x" * 40)
        self.assertTrue(a.is_closed(timeout=3))

    def test_oversized_packet(self):
        a = self.client()
        a.sock.sendall(struct.pack(">I", protocol.MAX_SIZE + 1))
        self.assertEqual(a.recv_type("ERROR")["code"], "PACKET_TOO_LARGE")
        self.assertTrue(a.is_closed(timeout=3))

    # SO_LINGER 0 để đóng kiểu RST, giống client bị rút mạng đột ngột
    def test_abrupt_disconnect_frees_the_name(self):
        name = unique()
        a = self.client(name)
        a.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        a.close()
        time.sleep(0.5)
        again = self.client(name)
        self.assertEqual(again.login_reply["type"], "AUTH_OK")

    def test_truncated_packet_does_not_break_server(self):
        a = self.client()
        a.sock.sendall(struct.pack(">I", 100) + b"chi co 10 b")
        a.close()
        time.sleep(0.3)
        self.assertEqual(self.client().login_reply["type"], "AUTH_OK")

    # alive thì trả PONG cho mọi PING, silent thì im lặng, hết IDLE_TIMEOUT server phải ngắt silent
    def test_silent_client_is_dropped_but_answering_client_stays(self):
        old = srv.IDLE_TIMEOUT
        srv.IDLE_TIMEOUT = 1.5
        try:
            silent = self.client()
            alive = self.client()
        finally:
            srv.IDLE_TIMEOUT = old
        end = time.time() + 3.5
        while time.time() < end:
            alive.sock.settimeout(0.3)
            try:
                packet = protocol.recv_packet(alive.sock)
            except socket.timeout:
                continue
            if packet.get("type") == "PING":
                alive.send({"type": "PONG"})
        self.assertTrue(silent.is_closed())
        alive.send({"type": "GET_USERS"})
        users = alive.recv_type("USERS")["users"]
        self.assertIn(alive.name, users)
        self.assertNotIn(silent.name, users)

    def test_unauthenticated_connection_times_out(self):
        old = srv.LOGIN_TIMEOUT
        srv.LOGIN_TIMEOUT = 1
        try:
            c = self.client(login=False)
            time.sleep(0.2)
        finally:
            srv.LOGIN_TIMEOUT = old
        self.assertTrue(c.is_closed(timeout=3))


class ConcurrencyTest(ServerTestCase):
    # đề yêu cầu server chịu được 10-20 client cùng lúc
    def test_twenty_clients_chat_at_the_same_time(self):
        n = 20
        clients = [self.client() for _ in range(n)]
        for c in clients:
            c.send({"type": "MSG", "to": "ALL", "text": f"hello from {c.name}"})
        for c in clients:
            got = set()
            while len(got) < n - 1:
                got.add(c.recv_type("MSG", timeout=5)["from"])
            self.assertEqual(got, {o.name for o in clients if o is not c})


if __name__ == "__main__":
    unittest.main()
