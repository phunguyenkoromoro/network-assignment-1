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
            return
        print("Đăng nhập thất bại:", reply["reason"])


def listen(sock, done):
    try:
        while True:
            packet = recv_packet(sock)
            if packet["type"] == "USERS":
                print("\nNgười đang online:", ", ".join(packet["users"]))
            else:
                print("\n", packet)
    except OSError:
        if not done.is_set():
            print("\nMất kết nối tới server")


def main():
    with socket.create_connection((HOST, PORT)) as sock:
        print(f"Đã kết nối tới {HOST}:{PORT}")
        login(sock)
        done = threading.Event()
        threading.Thread(target=listen, args=(sock, done), daemon=True).start()
        print("Lệnh: /users xem người online, /quit thoát")
        while True:
            command = input().strip()
            if command == "/users":
                send_packet(sock, {"type": "GET_USERS"})
            elif command == "/quit":
                done.set()
                send_packet(sock, {"type": "BYE"})
                break


if __name__ == "__main__":
    main()