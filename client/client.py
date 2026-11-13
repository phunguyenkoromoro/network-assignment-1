import base64
import getpass
import hashlib
import os
import socket
import threading
import uuid

from common.protocol import recv_packet, send_packet

HOST = "127.0.0.1"
PORT = 5000
CHUNK_SIZE = 4096
DOWNLOAD_DIR = "downloads"

online = set()
incoming = {}
aborted = set()


def login(sock):
    while True:
        username = input("Tên đăng nhập: ").strip()
        password = getpass.getpass("Mật khẩu: ")
        send_packet(sock, {"type": "AUTH", "username": username, "password": password})
        reply = recv_packet(sock)
        if reply["type"] == "AUTH_OK":
            print(reply["message"])
            return username
        print("Đăng nhập thất bại:", reply["reason"])


def send_file(sock, username, to, path):
    if not os.path.isfile(path):
        print("Không tìm thấy file:", path)
        return
    file_id = uuid.uuid4().hex[:8]
    name = os.path.basename(path)
    size = os.path.getsize(path)
    send_packet(sock, {"type": "FILE_START", "from": username, "to": to, "file_id": file_id,
                       "filename": name, "size": size, "chunk_size": CHUNK_SIZE})
    digest = hashlib.sha256()
    sent = 0
    seq = 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            if file_id in aborted:
                print("\nGửi file bị hủy")
                return
            digest.update(chunk)
            data = base64.b64encode(chunk).decode("ascii")
            send_packet(sock, {"type": "FILE_CHUNK", "file_id": file_id, "seq": seq, "data": data})
            seq += 1
            sent += len(chunk)
            print(f"\rĐang gửi {name}: {sent * 100 // size}%", end="", flush=True)
    send_packet(sock, {"type": "FILE_END", "file_id": file_id, "sha256": digest.hexdigest()})
    print(f"\nĐã gửi xong {name}")


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
    incoming[packet["file_id"]] = {"name": name, "size": packet["size"], "part": part,
                                   "file": open(part, "wb"), "digest": hashlib.sha256(),
                                   "seq": 0, "received": 0}
    print(f"\n{packet['from']} đang gửi file {name} ({packet['size']} byte)")


def cancel_incoming(file_id, reason):
    transfer = incoming.pop(file_id, None)
    if transfer:
        transfer["file"].close()
        os.remove(transfer["part"])
        print(f"Đã hủy nhận {transfer['name']}: {reason}")


def on_file_chunk(packet):
    transfer = incoming.get(packet["file_id"])
    if transfer is None:
        return
    if packet["seq"] != transfer["seq"]:
        cancel_incoming(packet["file_id"], "mảnh đến sai thứ tự")
        return
    data = base64.b64decode(packet["data"])
    transfer["file"].write(data)
    transfer["digest"].update(data)
    transfer["seq"] += 1
    transfer["received"] += len(data)


def on_file_end(packet):
    transfer = incoming.pop(packet["file_id"], None)
    if transfer is None:
        return
    transfer["file"].close()
    name = transfer["name"]
    if transfer["received"] == transfer["size"] and transfer["digest"].hexdigest() == packet["sha256"]:
        path = unique_path(name)
        os.replace(transfer["part"], path)
        print(f"\nĐã nhận xong {name}, SHA-256 khớp, lưu tại {path}")
    else:
        os.remove(transfer["part"])
        print(f"\nFile {name} bị hỏng (sai kích thước hoặc SHA-256), đã xóa")


def listen(sock, done):
    try:
        while True:
            packet = recv_packet(sock)
            kind = packet["type"]
            if kind == "USERS":
                online.clear()
                online.update(packet["users"])
                print("\nNgười đang online: " + ", ".join(packet["users"]))
            elif kind == "MSG":
                tag = "Chung" if packet["to"] == "ALL" else "Riêng"
                print(f"\n[{tag}] {packet['from']}: {packet['text']}")
            elif kind == "FILE_START":
                on_file_start(packet)
            elif kind == "FILE_CHUNK":
                on_file_chunk(packet)
            elif kind == "FILE_END":
                on_file_end(packet)
            elif kind == "ERROR":
                print("\nLỗi:", packet["message"])
                if "file_id" in packet:
                    aborted.add(packet["file_id"])
                    cancel_incoming(packet["file_id"], packet["message"])
            else:
                print("\n", packet)
    except OSError:
        if not done.is_set():
            print("\nMất kết nối tới server")


def main():
    with socket.create_connection((HOST, PORT)) as sock:
        print(f"Đã kết nối tới {HOST}:{PORT}")
        username = login(sock)
        done = threading.Event()
        threading.Thread(target=listen, args=(sock, done), daemon=True).start()
        print("Gõ chữ bình thường để chat chung")
        print("Lệnh: /msg <tên> <nội dung>, /send <tên> <đường dẫn file>, /users, /quit")
        try:
            while True:
                line = input().strip()
                if not line:
                    continue
                if line == "/users":
                    send_packet(sock, {"type": "GET_USERS"})
                elif line == "/quit":
                    done.set()
                    send_packet(sock, {"type": "BYE"})
                    break
                elif line.startswith("/msg "):
                    parts = line.split(" ", 2)
                    if len(parts) < 3:
                        print("Cú pháp: /msg <tên> <nội dung>")
                        continue
                    send_packet(sock, {"type": "MSG", "from": username, "to": parts[1], "text": parts[2]})
                elif line.startswith("/send "):
                    parts = line.split(" ", 2)
                    if len(parts) < 3:
                        print("Cú pháp: /send <tên> <đường dẫn file>")
                    elif parts[1] not in online:
                        print("Người nhận không online")
                    else:
                        send_file(sock, username, parts[1], parts[2].strip().strip('"'))
                elif line.startswith("/"):
                    print("Lệnh không hợp lệ")
                else:
                    send_packet(sock, {"type": "MSG", "from": username, "to": "ALL", "text": line})
        finally:
            for file_id in list(incoming):
                cancel_incoming(file_id, "client đã thoát")


if __name__ == "__main__":
    main()