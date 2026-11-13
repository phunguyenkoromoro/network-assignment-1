import hashlib
import json
import os
import socket
import threading
import time

from common.protocol import PacketTooLarge, recv_packet, send_packet

HOST = "0.0.0.0"
PORT = 5000
USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")

PING_INTERVAL = 15
IDLE_TIMEOUT = 30
LOGIN_TIMEOUT = 120
MAX_BAD_PACKETS = 5
MAX_FILE_SIZE = 100 * 1024 * 1024

clients = {}
transfers = {}
lock = threading.Lock()


class Session:
    def __init__(self, conn):
        self.conn = conn
        self.username = None
        self.send_lock = threading.Lock()

    def send(self, packet):
        with self.send_lock:
            try:
                send_packet(self.conn, packet)
            except OSError:
                self.close()
                raise

    def close(self):
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def error(code, message, **extra):
    return {"type": "ERROR", "code": code, "message": message, **extra}


def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def load_accounts():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_accounts():
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2)


accounts = load_accounts()


def authenticate(session, username, password):
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError("username và password phải là chuỗi")
    if not username.isalnum() or len(username) > 20 or username.upper() == "ALL":
        return "Tên không hợp lệ"
    if not password:
        return "Mật khẩu không được để trống"
    with lock:
        if username in clients:
            return "Tên đã được dùng"
        if username not in accounts:
            accounts[username] = hash_password(password)
            save_accounts()
        elif accounts[username] != hash_password(password):
            return "Sai mật khẩu"
        clients[username] = session
        session.username = username
    return None


def broadcast_users():
    with lock:
        users = list(clients)
        sessions = list(clients.values())
    for session in sessions:
        try:
            session.send({"type": "USERS", "users": users})
        except OSError:
            pass


def try_login(session, packet):
    username = packet.get("username", "")
    reason = authenticate(session, username, packet.get("password", ""))
    if reason:
        session.send({"type": "AUTH_FAIL", "reason": reason})
        return
    session.conn.settimeout(IDLE_TIMEOUT)
    session.send({"type": "AUTH_OK", "message": f"Chào mừng {username}"})
    broadcast_users()


def handle_msg(session, packet):
    to = packet.get("to")
    text = packet.get("text")
    if not isinstance(to, str) or not to or not isinstance(text, str) or not text:
        raise ValueError("MSG thiếu người nhận hoặc nội dung")
    out = {"type": "MSG", "from": session.username, "to": to, "text": text}
    with lock:
        if to == "ALL":
            targets = [s for name, s in clients.items() if name != session.username]
        elif to in clients:
            targets = [clients[to]]
        else:
            targets = None
    if targets is None:
        session.send(error("USER_OFFLINE", "Người nhận không online"))
        return
    for target in targets:
        try:
            target.send(out)
        except OSError:
            pass


def handle_file(session, packet):
    kind = packet["type"]
    file_id = packet.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        raise ValueError("Gói file thiếu file_id")
    if kind == "FILE_START":
        size = packet.get("size")
        if not isinstance(packet.get("filename"), str) or not isinstance(size, int) or size < 0:
            raise ValueError("FILE_START sai filename hoặc size")
        if size > MAX_FILE_SIZE:
            session.send(error("FILE_ERROR", f"File vượt quá giới hạn {MAX_FILE_SIZE // (1024 * 1024)} MB",
                               file_id=file_id))
            return
    key = (session.username, file_id)
    with lock:
        if kind == "FILE_START":
            recipient = packet.get("to")
            if isinstance(recipient, str) and recipient in clients:
                transfers[key] = recipient
        else:
            recipient = transfers.get(key)
            if kind == "FILE_END":
                transfers.pop(key, None)
        target = clients.get(recipient) if isinstance(recipient, str) else None
    if target is None:
        if kind == "FILE_START":
            session.send(error("USER_OFFLINE", "Người nhận không online"))
        elif recipient is not None:
            with lock:
                transfers.pop(key, None)
            session.send(error("FILE_ERROR", "Người nhận đã ngắt kết nối", file_id=file_id))
        return
    try:
        target.send({**packet, "from": session.username})
    except OSError:
        pass


def cancel_transfers(username):
    with lock:
        dropped = [(fid, to) for (sender, fid), to in transfers.items() if sender == username]
        for fid, _ in dropped:
            del transfers[(username, fid)]
        targets = [(fid, clients.get(to)) for fid, to in dropped]
    for fid, target in targets:
        if target:
            try:
                target.send(error("FILE_ERROR", "Người gửi đã ngắt kết nối giữa chừng", file_id=fid))
            except OSError:
                pass


def dispatch(session, packet):
    kind = packet["type"]
    if session.username is None:
        if kind == "AUTH":
            try_login(session, packet)
        else:
            session.send(error("NOT_AUTHENTICATED", "Cần đăng nhập trước"))
    elif kind == "GET_USERS":
        with lock:
            users = list(clients)
        session.send({"type": "USERS", "users": users})
    elif kind == "MSG":
        handle_msg(session, packet)
    elif kind in ("FILE_START", "FILE_CHUNK", "FILE_END"):
        handle_file(session, packet)
    elif kind == "PING":
        session.send({"type": "PONG"})
    elif kind != "PONG":
        raise ValueError(f"Loại gói không hợp lệ: {kind}")


def handle_client(conn, addr):
    print(f"[+] {addr} đã kết nối")
    session = Session(conn)
    conn.settimeout(LOGIN_TIMEOUT)
    bad = 0
    try:
        while True:
            try:
                packet = recv_packet(conn)
                if not isinstance(packet, dict) or not isinstance(packet.get("type"), str):
                    raise ValueError("Gói không phải object hoặc thiếu type")
                if packet["type"] == "BYE":
                    break
                dispatch(session, packet)
            except PacketTooLarge:
                session.send(error("PACKET_TOO_LARGE", "Gói vượt quá 1 MB"))
                break
            except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as e:
                bad += 1
                print(f"[!] {addr} gửi gói lỗi ({bad}/{MAX_BAD_PACKETS}): {e!r}")
                session.send(error("BAD_PACKET", "Gói sai định dạng hoặc thiếu trường"))
                if bad >= MAX_BAD_PACKETS:
                    break
    except OSError:
        pass
    finally:
        if session.username:
            with lock:
                clients.pop(session.username, None)
            cancel_transfers(session.username)
            broadcast_users()
        conn.close()
        print(f"[-] {addr} đã ngắt kết nối")


def heartbeat():
    while True:
        time.sleep(PING_INTERVAL)
        with lock:
            sessions = list(clients.values())
        for session in sessions:
            try:
                session.send({"type": "PING"})
            except OSError:
                pass


def main():
    threading.Thread(target=heartbeat, daemon=True).start()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen()
        print(f"Server đang chạy ở cổng {PORT}")
        try:
            while True:
                conn, addr = server.accept()
                threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            print("\nServer đã dừng")


if __name__ == "__main__":
    main()