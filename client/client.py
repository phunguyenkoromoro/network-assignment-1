# client chat: kết nối tới server, đăng nhập, chat, gửi / nhận file
# file này lo phần mạng + logic, còn phần hiển thị nằm ở client/UI.py
# chạy: python -m client.client [địa_chỉ_server] [cổng]

import base64
import getpass
import hashlib
import os
import socket
import sys
import threading
import uuid

try:
    from client.UI import ChatUI, format_size
except ModuleNotFoundError as e:
    if e.name != "prompt_toolkit":
        raise
    raise SystemExit("Thiếu thư viện prompt_toolkit, chạy: pip install -r requirements.txt")
from common.protocol import DecryptError, recv_packet, send_packet

HOST = "127.0.0.1"
PORT = 5000
# mỗi mảnh file 4096 byte
CHUNK_SIZE = 4096
DOWNLOAD_DIR = "downloads"
# 45s không nghe gì từ server thì coi như mất kết nối
HEARTBEAT_TIMEOUT = 45

# ui: đối tượng ChatUI (trong UI.py), tạo sau khi đăng nhập xong. incoming: các file đang nhận
# aborted: file mình gửi mà server báo lỗi (vd người nhận rớt)
ui = None
incoming = {}
aborted = set()
# luồng chính, thread gửi file và thread nghe cùng gửi vào 1 socket nên cũng phải khóa như bên
# server
send_lock = threading.Lock()


def send(sock, packet):
    with send_lock:
        send_packet(sock, packet)


# hỏi tên + mật khẩu tới khi đăng nhập được, trả về username
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


# gửi file: FILE_START -> nhiều FILE_CHUNK -> FILE_END
# chạy trong thread riêng để vừa gửi file vừa chat được
def send_file(sock, username, to, path):
    if not os.path.isfile(path):
        ui.error(f"Không tìm thấy file: {path}")
        return
    file_id = uuid.uuid4().hex[:8]
    name = os.path.basename(path)
    size = os.path.getsize(path)
    ui.progress[file_id] = {"name": name, "dir": "send", "done": 0, "total": size}
    try:
        send(
            sock,
            {
                "type": "FILE_START",
                "from": username,
                "to": to,
                "file_id": file_id,
                "filename": name,
                "size": size,
                "chunk_size": CHUNK_SIZE,
            },
        )
        ui.notice(f"Bắt đầu gửi {name} ({format_size(size)}) cho {to}")
        # hash cả file, cuối cùng gửi kèm để bên nhận kiểm tra file có bị hỏng không
        digest = hashlib.sha256()
        sent = 0
        seq = 0
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                # server báo người nhận đã rớt thì dừng gửi
                if file_id in aborted:
                    ui.notice(f"Đã hủy gửi {name}")
                    return
                digest.update(chunk)
                # JSON không chứa được byte thô nên mã hóa base64 (nặng hơn khoảng 33%, đánh đổi cho
                # đơn giản)
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


# trả về đường dẫn trong downloads/, trùng tên thì thêm (1), (2)...
def unique_path(name):
    base, ext = os.path.splitext(name)
    path = os.path.join(DOWNLOAD_DIR, name)
    n = 1
    while os.path.exists(path):
        path = os.path.join(DOWNLOAD_DIR, f"{base} ({n}){ext}")
        n += 1
    return path


# bắt đầu nhận file: mở file tạm .part, nhận tới đâu ghi tới đó
def on_file_start(packet):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    # chỉ lấy phần tên file, bỏ đường dẫn đi (vd ../../x) để không bị ghi ra ngoài downloads/
    name = packet["filename"].replace("\\", "/").split("/")[-1] or "file"
    part = os.path.join(DOWNLOAD_DIR, uuid.uuid4().hex + ".part")
    file_id = packet["file_id"]
    incoming[file_id] = {
        "name": name,
        "size": packet["size"],
        "part": part,
        "file": open(part, "wb"),
        "digest": hashlib.sha256(),
        "seq": 0,
        "received": 0,
    }
    ui.progress[file_id] = {"name": name, "dir": "recv", "done": 0, "total": packet["size"]}
    ui.notice(f"{packet['from']} đang gửi file {name} ({format_size(packet['size'])})")


# hủy file đang nhận dở: đóng + xóa file tạm
def cancel_incoming(file_id, reason):
    transfer = incoming.pop(file_id, None)
    ui.progress.pop(file_id, None)
    if transfer:
        transfer["file"].close()
        os.remove(transfer["part"])
        ui.error(f"Đã hủy nhận {transfer['name']}: {reason}")


# nhận 1 mảnh: kiểm tra đúng thứ tự (seq) và không vượt kích thước đã khai báo rồi mới ghi
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


# nhận xong: so kích thước + SHA-256, khớp thì đổi tên file tạm thành file thật, không khớp thì xóa
def on_file_end(packet):
    file_id = packet["file_id"]
    transfer = incoming.pop(file_id, None)
    ui.progress.pop(file_id, None)
    if transfer is None:
        return
    transfer["file"].close()
    name = transfer["name"]
    if (
        transfer["received"] == transfer["size"]
        and transfer["digest"].hexdigest() == packet["sha256"]
    ):
        path = unique_path(name)
        os.replace(transfer["part"], path)
        ui.notice(f"Đã nhận xong {name}, SHA-256 khớp, lưu tại {path}")
    else:
        os.remove(transfer["part"])
        ui.error(f"File {name} bị hỏng (sai kích thước hoặc SHA-256), đã xóa")


# cập nhật danh sách online, so với danh sách cũ để báo ai vừa vào / vừa thoát
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


# xử lý 1 gói từ server theo loại
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
    # heartbeat: trả PONG để server biết mình còn sống
    elif kind == "PING":
        send(sock, {"type": "PONG"})
    elif kind == "ERROR":
        ui.error(packet["message"])
        if "file_id" in packet:
            aborted.add(packet["file_id"])
            cancel_incoming(packet["file_id"], packet["message"])


# thread nghe server: nhận gói rồi hiển thị, mất kết nối thì báo và thoát
def listen(sock, done, lost):
    try:
        while True:
            packet = recv_packet(sock)
            try:
                handle_packet(sock, packet)
            except (KeyError, TypeError, ValueError, OSError) as e:
                ui.error(f"Không xử lý được gói từ server: {e!r}")
    except (OSError, ValueError):
        # done được bật khi mình tự thoát, lúc đó khỏi báo mất kết nối
        if not done.is_set():
            lost.set()
            ui.error("Mất kết nối tới server")
            ui.interrupt()


# xử lý các lệnh bắt đầu bằng dấu /
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
            # gửi trong thread riêng để đang gửi file vẫn gõ lệnh / chat được
            args = (sock, username, target, path)
            threading.Thread(target=send_file, args=args, daemon=True).start()
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
        # done: mình chủ động thoát, lost: mất kết nối tới server
        done = threading.Event()
        lost = threading.Event()
        try:
            username = login(sock)
            try:
                ui = ChatUI(username)
            except Exception as e:
                print(f"Terminal không hỗ trợ giao diện này ({e}).")
                print("Hãy chạy trong PowerShell, CMD hoặc terminal của VS Code.")
                return
            sock.settimeout(HEARTBEAT_TIMEOUT)
            # trong khối này tin nhắn in ra từ thread khác sẽ nằm phía trên dòng đang gõ, không làm
            # hỏng chữ
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
            print("\nKhông giải mã được dữ liệu từ server, kiểm tra file secret.key giống nhau")
        except (OSError, ValueError):
            if not lost.is_set():
                print("\nMất kết nối tới server")
        except (KeyboardInterrupt, EOFError):
            if not lost.is_set():
                print("\nĐã thoát")
        finally:
            done.set()
            try:
                # báo server là mình thoát lịch sự
                send(sock, {"type": "BYE"})
            except OSError:
                pass
            # xóa file đang nhận dở
            for file_id in list(incoming):
                cancel_incoming(file_id, "client đã thoát")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        HOST = sys.argv[1]
    if len(sys.argv) > 2:
        PORT = int(sys.argv[2])
    main()