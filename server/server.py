import socket

from common.protocol import recv_packet, send_packet

HOST = "0.0.0.0"
PORT = 5055


def handle_client(conn, addr):
    print(f"[+] {addr} đã kết nối")
    packet = recv_packet(conn)
    if packet["type"] == "PING":
        send_packet(conn, {"type": "PONG"})
    conn.close()
    print(f"[-] {addr} đã ngắt kết nối")


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen()
        print(f"Server đang chạy tại cổng {PORT}")
        while True:
            conn, addr = server.accept()
            handle_client(conn, addr)


if __name__ == "__main__":
    main()
