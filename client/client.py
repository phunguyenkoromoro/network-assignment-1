import socket

from common.protocol import recv_packet, send_packet

HOST = "127.0.0.1"
PORT = 5000


def main():
    with socket.create_connection((HOST, PORT)) as sock:
        print(f"Đã kết nối tới {HOST}:{PORT}")
        send_packet(sock, {"type": "PING"})
        print("Server trả lời:", recv_packet(sock))
        input("Nhấn Enter để thoát...")
        send_packet(sock, {"type": "BYE"})


if __name__ == "__main__":
    main()