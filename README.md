# Ứng dụng cộng tác Client-Server

Bài tập lớn môn Mạng Máy tính: chat và gửi file giữa nhiều client thông qua server.

## Tiến độ

- [x] Bước 1: thiết kế giao thức (`docs/protocol.md`)
- [x] Bước 2: handshake TCP cơ bản (client gửi `PING`, server trả `PONG`)
- [ ] Bước 3: đa luồng
- [ ] Bước 4: đăng nhập và quản lý người dùng
- [ ] Bước 5: gửi tin nhắn chung và riêng
- [ ] Bước 6: gửi file
- [ ] Bước 7: xử lý lỗi và ngắt kết nối
- [ ] Bước 8: mã hóa
- [ ] Bước 9: giao diện CLI
- [ ] Bước 10: test và dọn code

## Yêu cầu

- Python 3.8 trở lên
- Chưa cần cài thư viện nào. Từ bước 8 sẽ cần `cryptography`.

## Cách chạy

Chạy từ thư mục gốc của repo, mở 2 terminal.

Terminal 1, chạy server:
```
python -m server.server
```

Terminal 2, chạy client:
```
python -m client.client
```

Kết quả mong đợi:
- Server in ra `[+] ... đã kết nối` rồi `[-] ... đã ngắt kết nối`.
- Client in ra `Server trả lời: {'type': 'PONG'}`.

Nếu gõ `python` không chạy được trên Windows thì thử `py`.

## Cấu trúc thư mục

```
network-assignment-1/
├── server/
│   └── server.py
├── client/
│   └── client.py
├── common/
│   └── protocol.py      # hàm đóng gói và đọc gói dùng chung
├── docs/
│   └── protocol.md      # đặc tả giao thức
└── README.md
```
