import getpass
import socket
import threading

from common.protocol import recv_packet, send_packet

HOST = "127.0.0.1"
PORT = 5000


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


def listen(sock, done):
    try:
        while True:
            packet = recv_packet(sock)
            kind = packet["type"]
            if kind == "USERS":
                print("\nNgười đang online: " + ", ".join(packet["users"]))
            elif kind == "MSG":
                tag = "Chung" if packet["to"] == "ALL" else "Riêng"
                print(f"\n[{tag}] {packet['from']}: {packet['text']}")
            elif kind == "ERROR":
                print("\nLỗi:", packet["message"])
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
        print("Lệnh: /msg <tên> <nội dung>, /users, /quit")
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
            elif line.startswith("/"):
                print("Lệnh không hợp lệ")
            else:
                send_packet(sock, {"type": "MSG", "from": username, "to": "ALL", "text": line})


if __name__ == "__main__":
    main()