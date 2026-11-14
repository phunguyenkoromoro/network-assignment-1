import base64
import getpass
import hashlib
import os
import socket
import threading
import uuid

try:
    from client.UI import ChatUI, format_size
except ImportError:
    raise SystemExit("Thiếu thư viện prompt_toolkit, chạy: pip install -r requirements.txt")
from common.protocol import DecryptError, recv_packet, send_packet

HOST = "127.0.0.1"
PORT = 5000
CHUNK_SIZE = 4096
DOWNLOAD_DIR = "downloads"
HEARTBEAT_TIMEOUT = 45

ui = None
incoming = {}
aborted = set()
send_lock = threading.Lock()


def send(sock, packet):
    with send_lock:
        send_packet(sock, packet)


def login(sock):
    while True:
        username = input("Tên đăng nhập: ").strip()
        password = getpass.getpass("Mật khẩu: ")
        send(sock, {"type": "AUTH", "username": username, "password": password})
        reply = recv_packet(sock)
        if reply["type"] == "AUTH_OK":
            print(reply["message"])
            return username
        print("Đăng nhập thất bại:", reply["reason"])


def send_file(sock, username, to, path):
    if not os.path.isfile(path):
        ui.error(f"Không tìm thấy file: {path}")
        return
    file_id = uuid.uuid4().hex[:8]
    name = os.path.basename(path)
    size = os.path.getsize(path)
    ui.progress[file_id] = {"name": name, "dir": "send", "done": 0, "total": size}
    try:
        send(sock, {"type": "FILE_START", "from": username, "to": to, "file_id": file_id,
                    "filename": name, "size": size, "chunk_size": CHUNK_SIZE})
        ui.notice(f"Bắt đầu gửi {name} ({format_size(size)}) cho {to}")
        digest = hashlib.sha256()
        sent = 0
        seq = 0
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                if file_id in aborted:
                    ui.notice(f"Đã hủy gửi {name}")
                    return
                digest.update(chunk)
                data = base64.b64encode(chunk).decode("ascii")
                send(sock, {"type": "FILE_CHUNK", "file_id": file_id, "seq": seq, "data": data})
                seq += 1
                sent += len(chunk)
                ui.progress[file_id]["done"] = sent
        send(sock, {"type": "FILE_END", "file_id": file_id, "sha256": digest.hexdigest()})
        ui.notice(f"Đã gửi xong {name}")
    except OSError:
        ui.error(f"Gửi {name} thất bại, mất kết nối tới server")
    finally:
        ui.progress.pop(file_id, None)


def unique_path(name):
    base, ext = os.path.splitext(name)
    path = os.path.join(DOWNLOAD_DIR, name)
    n = 1
    while os.path.exists(path):
        path = os.path.join(DOWNLOAD_DIR, f"{base} ({n}){ext}")
        n += 1
    return path


def on_file_start(packet):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    name = packet["filename"].replace("\\", "/").split("/")[-1] or "file"
    part = os.path.join(DOWNLOAD_DIR, uuid.uuid4().hex + ".part")
    file_id = packet["file_id"]
    incoming[file_id] = {"name": name, "size": packet["size"], "part": part,
                         "file": open(part, "wb"), "digest": hashlib.sha256(),
                         "seq": 0, "received": 0}
    ui.progress[file_id] = {"name": name, "dir": "recv", "done": 0, "total": packet["size"]}
    ui.notice(f"{packet['from']} đang gửi file {name} ({format_size(packet['size'])})")


def cancel_incoming(file_id, reason):
    transfer = incoming.pop(file_id, None)
    ui.progress.pop(file_id, None)
    if transfer:
        transfer["file"].close()
        os.remove(transfer["part"])
        ui.error(f"Đã hủy nhận {transfer['name']}: {reason}")


def on_file_chunk(packet):
    file_id = packet["file_id"]
    transfer = incoming.get(file_id)
    if transfer is None:
        return
    if packet["seq"] != transfer["seq"]:
        cancel_incoming(file_id, "mảnh đến sai thứ tự")
        return
    data = base64.b64decode(packet["data"])
    if transfer["received"] + len(data) > transfer["size"]:
        cancel_incoming(file_id, "nhận nhiều hơn kích thước đã khai báo")
        return
    try:
        transfer["file"].write(data)
    except OSError as e:
        cancel_incoming(file_id, f"không ghi được file ({e.strerror})")
        return
    transfer["digest"].update(data)
    transfer["seq"] += 1
    transfer["received"] += len(data)
    ui.progress[file_id]["done"] = transfer["received"]


def on_file_end(packet):
    file_id = packet["file_id"]
    transfer = incoming.pop(file_id, None)
    ui.progress.pop(file_id, None)
    if transfer is None:
        return
    transfer["file"].close()
    name = transfer["name"]
    if transfer["received"] == transfer["size"] and transfer["digest"].hexdigest() == packet["sha256"]:
        path = unique_path(name)
        os.replace(transfer["part"], path)
        ui.notice(f"Đã nhận xong {name}, SHA-256 khớp, lưu tại {path}")
    else:
        os.remove(transfer["part"])
        ui.error(f"File {name} bị hỏng (sai kích thước hoặc SHA-256), đã xóa")


def update_online(users):
    new = frozenset(users)
    if ui.online:
        for name in sorted(new - ui.online):
            ui.notice(f"{name} đã vào")
        for name in sorted(ui.online - new):
            ui.notice(f"{name} đã thoát")
    else:
        ui.notice("Đang online: " + ", ".join(sorted(new)))
    ui.online = new


def handle_packet(sock, packet):
    kind = packet["type"]
    if kind == "USERS":
        update_online(packet["users"])
    elif kind == "MSG":
        if packet["to"] == "ALL":
            ui.chat(packet["from"], packet["text"])
        else:
            ui.private_in(packet["from"], packet["text"])
    elif kind == "FILE_START":
        on_file_start(packet)
    elif kind == "FILE_CHUNK":
        on_file_chunk(packet)
    elif kind == "FILE_END":
        on_file_end(packet)
    elif kind == "PING":
        send(sock, {"type": "PONG"})
    elif kind == "ERROR":
        ui.error(packet["message"])
        if "file_id" in packet:
            aborted.add(packet["file_id"])
            cancel_incoming(packet["file_id"], packet["message"])


def listen(sock, done, lost):
    try:
        while True:
            packet = recv_packet(sock)
            try:
                handle_packet(sock, packet)
            except (KeyError, TypeError, ValueError, OSError) as e:
                ui.error(f"Không xử lý được gói từ server: {e!r}")
    except (OSError, ValueError):
        if not done.is_set():
            lost.set()
            ui.error("Mất kết nối tới server")
            ui.interrupt()


def handle_command(sock, username, line):
    command, _, rest = line.partition(" ")
    if command == "/users":
        ui.users()
    elif command == "/help":
        ui.help()
    elif command == "/clear":
        ui.clear()
    elif command == "/msg":
        target, _, text = rest.partition(" ")
        text = text.strip()
        if not target or not text:
            ui.error("Cú pháp: /msg <tên> <nội dung>")
        elif target not in ui.online:
            ui.error("Người nhận không online")
        else:
            send(sock, {"type": "MSG", "from": username, "to": target, "text": text})
            ui.private_out(target, text)
    elif command == "/send":
        target, _, path = rest.partition(" ")
        path = path.strip().strip('"')
        if not target or not path:
            ui.error("Cú pháp: /send <tên> <đường dẫn file>")
        elif target not in ui.online:
            ui.error("Người nhận không online")
        else:
            threading.Thread(target=send_file, args=(sock, username, target, path), daemon=True).start()
    else:
        ui.error("Lệnh không hợp lệ, gõ /help để xem danh sách lệnh")


def main():
    global ui
    try:
        sock = socket.create_connection((HOST, PORT), timeout=5)
    except OSError:
        print(f"Không kết nối được tới {HOST}:{PORT}, server đã chạy chưa?")
        return
    with sock:
        sock.settimeout(None)
        print(f"Đã kết nối tới {HOST}:{PORT}")
        done = threading.Event()
        lost = threading.Event()
        try:
            username = login(sock)
            try:
                ui = ChatUI(username)
            except Exception as e:
                print(f"Terminal không hỗ trợ giao diện này ({e}). Hãy chạy trong PowerShell, CMD hoặc terminal của VS Code.")
                return
            sock.settimeout(HEARTBEAT_TIMEOUT)
            with ui.context():
                threading.Thread(target=listen, args=(sock, done, lost), daemon=True).start()
                ui.notice("Gõ /help để xem lệnh, gõ chữ bình thường để chat chung")
                while True:
                    line = ui.read().strip()
                    if lost.is_set() or line == "/quit":
                        break
                    if not line:
                        continue
                    if line.startswith("/"):
                        handle_command(sock, username, line)
                    else:
                        send(sock, {"type": "MSG", "from": username, "to": "ALL", "text": line})
                        ui.own_chat(line)
        except DecryptError:
            print("\nKhông giải mã được dữ liệu từ server, kiểm tra file secret.key giống nhau ở server và client")
        except (OSError, ValueError):
            if not lost.is_set():
                print("\nMất kết nối tới server")
        except (KeyboardInterrupt, EOFError):
            if not lost.is_set():
                print("\nĐã thoát")
        finally:
            done.set()
            try:
                send(sock, {"type": "BYE"})
            except OSError:
                pass
            for file_id in list(incoming):
                cancel_incoming(file_id, "client đã thoát")


if __name__ == "__main__":
    main()