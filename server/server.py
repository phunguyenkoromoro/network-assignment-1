# server chat: nhận kết nối TCP, quản lý người dùng, chuyển tiếp tin nhắn và file
# mỗi client chạy trong 1 thread riêng (handle_client), server không lưu tin nhắn hay file, chỉ
# forward thôi

import hashlib
import json
import os
import socket
import sys
import threading
import time

from common.protocol import PacketTooLarge, recv_packet, send_packet

HOST = "0.0.0.0"
PORT = 5000
USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")

# heartbeat: server ping mỗi 15s, đăng nhập rồi mà 30s không nghe gì thì coi như mất kết nối
# chưa đăng nhập thì cho chờ lâu hơn (120s) vì còn phải gõ tên với mật khẩu
PING_INTERVAL = 15
IDLE_TIMEOUT = 30
LOGIN_TIMEOUT = 120
# gửi quá 5 gói lỗi thì ngắt, file lớn hơn 100 MB thì từ chối
MAX_BAD_PACKETS = 5
MAX_FILE_SIZE = 100 * 1024 * 1024

# clients: username -> Session, danh sách người đang online
# transfers: (người gửi, file_id) -> người nhận, để biết FILE_CHUNK phải chuyển cho ai
# lock: nhiều thread cùng sửa mấy dict này nên phải khóa, không là lỗi lung tung
clients = {}
transfers = {}
lock = threading.Lock()


# 1 kết nối client: socket, địa chỉ (IP, cổng), tên đăng nhập và cái khóa gửi riêng
class Session:
    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.username = None
        # nhiều thread có thể cùng gửi vào 1 socket (vd broadcast USERS lúc thread của client đó
        # đang trả lời)
        # không khóa thì 2 gói bị trộn byte vào nhau
        self.send_lock = threading.Lock()

    def send(self, packet):
        with self.send_lock:
            try:
                send_packet(self.conn, packet)
            except OSError:
                # gửi lỗi giữa chừng thì stream có thể bị lệch, nên đóng luôn kết nối này cho chắc
                self.close()
                raise

    def close(self):
        try:
            # shutdown để đánh thức thread đang kẹt ở recv
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


# tạo gói ERROR, extra là các trường thêm (vd file_id)
def error(code, message, **extra):
    return {"type": "ERROR", "code": code, "message": message, **extra}


# băm SHA-256 rồi mới lưu, không lưu mật khẩu gốc (chưa salt, đơn giản hóa cho bài tập)
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


# username -> hash mật khẩu, nạp từ users.json lúc server khởi động
accounts = load_accounts()


# in ra console server: ai đang online + IP:cổng (đề yêu cầu server phải theo dõi IP và cổng)
def log_online():
    with lock:
        info = ", ".join(f"{name} ({s.addr[0]}:{s.addr[1]})" for name, s in clients.items())
    print(f"[i] Đang online: {info or '-'}")


# kiểm tra đăng nhập, trả về lý do thất bại, hoặc None nếu thành công
def authenticate(session, username, password):
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError("username và password phải là chuỗi")
    # ALL bị cấm làm tên vì ALL là đích của chat chung
    if not username.isalnum() or len(username) > 20 or username.upper() == "ALL":
        return "Tên không hợp lệ"
    if not password:
        return "Mật khẩu không được để trống"
    with lock:
        if username in clients:
            return "Tên đã được dùng"
        # tên mới thì tự đăng ký luôn
        if username not in accounts:
            accounts[username] = hash_password(password)
            save_accounts()
        elif accounts[username] != hash_password(password):
            return "Sai mật khẩu"
        clients[username] = session
        session.username = username
    return None


# gửi danh sách online mới cho tất cả mọi người
def broadcast_users():
    with lock:
        users = list(clients)
        sessions = list(clients.values())
    # gửi ngoài lock nha, để 1 client chậm không giữ lock của cả server
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
    # đăng nhập xong thì đổi sang timeout ngắn hơn (IDLE_TIMEOUT)
    session.conn.settimeout(IDLE_TIMEOUT)
    session.send({"type": "AUTH_OK", "message": f"Chào mừng {username}"})
    broadcast_users()
    log_online()


# chuyển tin nhắn: to = "ALL" là chat chung, còn lại là chat riêng
def handle_msg(session, packet):
    to = packet.get("to")
    text = packet.get("text")
    if not isinstance(to, str) or not to or not isinstance(text, str) or not text:
        raise ValueError("MSG thiếu người nhận hoặc nội dung")
    # from do server tự điền theo tài khoản của kết nối, không tin from client gửi, vậy mới không
    # giả mạo được
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


# chuyển tiếp FILE_START, FILE_CHUNK, FILE_END tới người nhận
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
            limit = MAX_FILE_SIZE // (1024 * 1024)
            session.send(error("FILE_ERROR", f"File vượt quá giới hạn {limit} MB", file_id=file_id))
            return
    # FILE_CHUNK với FILE_END không có trường "to" nên server nhớ người nhận từ lúc FILE_START
    # key có cả tên người gửi để người khác không chen vào file của mình được
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
        # đang truyền dở mà người nhận đã rớt
        elif recipient is not None:
            with lock:
                transfers.pop(key, None)
            session.send(error("FILE_ERROR", "Người nhận đã ngắt kết nối", file_id=file_id))
        return
    try:
        target.send({**packet, "from": session.username})
    except OSError:
        pass


# người gửi rớt giữa chừng thì báo FILE_ERROR cho người đang nhận file của họ
def cancel_transfers(username):
    with lock:
        dropped = [(fid, to) for (sender, fid), to in transfers.items() if sender == username]
        for fid, _ in dropped:
            del transfers[(username, fid)]
        targets = [(fid, clients.get(to)) for fid, to in dropped]
    for fid, target in targets:
        if target:
            try:
                target.send(
                    error("FILE_ERROR", "Người gửi đã ngắt kết nối giữa chừng", file_id=fid)
                )
            except OSError:
                pass


# xử lý 1 gói theo loại, gói không hợp lệ thì ném ValueError
def dispatch(session, packet):
    kind = packet["type"]
    # chưa đăng nhập thì chỉ nhận AUTH thôi
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
    # PONG là trả lời heartbeat, không cần làm gì
    elif kind != "PONG":
        raise ValueError(f"Loại gói không hợp lệ: {kind}")


# vòng lặp của thread phục vụ 1 client, chạy tới khi client rời đi
def handle_client(conn, addr):
    print(f"[+] {addr} đã kết nối")
    session = Session(conn, addr)
    conn.settimeout(LOGIN_TIMEOUT)
    # đếm số gói lỗi của kết nối này
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
            # chưa đọc phần thân nên không đồng bộ lại stream được, phải ngắt luôn
            except PacketTooLarge:
                session.send(error("PACKET_TOO_LARGE", "Gói vượt quá 1 MB"))
                break
            # gói sai định dạng thì báo lỗi và giữ kết nối, lặp lại nhiều lần mới ngắt
            except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as e:
                bad += 1
                print(f"[!] {addr} gửi gói lỗi ({bad}/{MAX_BAD_PACKETS}): {e!r}")
                session.send(error("BAD_PACKET", "Gói sai định dạng hoặc thiếu trường"))
                if bad >= MAX_BAD_PACKETS:
                    break
    except OSError:
        pass
    # dọn dẹp: mất kết nối / timeout / broken pipe nào cũng chạy xuống đây
    # xóa user khỏi danh sách, hủy file đang truyền dở rồi báo cho mọi người
    finally:
        if session.username:
            with lock:
                clients.pop(session.username, None)
            cancel_transfers(session.username)
            broadcast_users()
            log_online()
        conn.close()
        print(f"[-] {addr} đã ngắt kết nối")


# thread nền: gửi PING định kỳ cho mọi người đã đăng nhập
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


# mở cổng, client nào tới thì tạo 1 thread riêng cho client đó
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
    if len(sys.argv) > 1:
        PORT = int(sys.argv[1])
    main()