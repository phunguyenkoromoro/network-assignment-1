import socket

from common.protocol import recv_packet, send_packet

HOST = "127.0.0.1"
PORT = 5055


def main():
    with socket.create_connection((HOST, PORT)) as sock:
        print(f"Đã kết nối tới {HOST}:{PORT}")
        send_packet(sock, {"type": "PING"})
        reply = recv_packet(sock)
        print("Server:", reply)


if __name__ == "__main__":
    main()
