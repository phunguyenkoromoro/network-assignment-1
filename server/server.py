import hashlib
import json
import os
import socket
import threading

from common.protocol import recv_packet, send_packet

HOST = "0.0.0.0"
PORT = 5000
USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")

clients = {}
lock = threading.Lock()


class Session:
    def __init__(self, conn):
        self.conn = conn
        self.send_lock = threading.Lock()

    def send(self, packet):
        with self.send_lock:
            send_packet(self.conn, packet)


def error(code, message):
    return {"type": "ERROR", "code": code, "message": message}


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
        return None
    session.send({"type": "AUTH_OK", "message": f"Chào mừng {username}"})
    broadcast_users()
    return username


def handle_msg(session, username, packet):
    to = packet.get("to")
    text = packet.get("text")
    if not to or not isinstance(text, str) or not text:
        session.send(error("BAD_PACKET", "MSG thiếu người nhận hoặc nội dung"))
        return
    out = {"type": "MSG", "from": username, "to": to, "text": text}
    with lock:
        if to == "ALL":
            targets = [s for name, s in clients.items() if name != username]
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


def handle_client(conn, addr):
    print(f"[+] {addr} đã kết nối")
    session = Session(conn)
    username = None
    try:
        while True:
            packet = recv_packet(conn)
            kind = packet.get("type")
            if kind == "BYE":
                break
            if username is None:
                if kind == "AUTH":
                    username = try_login(session, packet)
                else:
                    session.send(error("NOT_AUTHENTICATED", "Cần đăng nhập trước"))
            elif kind == "GET_USERS":
                with lock:
                    users = list(clients)
                session.send({"type": "USERS", "users": users})
            elif kind == "MSG":
                handle_msg(session, username, packet)
            elif kind == "PING":
                session.send({"type": "PONG"})
    except ConnectionError:
        pass
    finally:
        if username:
            with lock:
                clients.pop(username, None)
            broadcast_users()
        conn.close()
        print(f"[-] {addr} đã ngắt kết nối")


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen()
        print(f"Server đang chạy ở cổng {PORT}")
        while True:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()