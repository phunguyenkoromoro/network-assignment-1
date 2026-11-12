# Ứng dụng cộng tác Client-Server

Bài tập lớn môn Mạng Máy tính: chat và gửi file giữa nhiều client thông qua server.

## Tiến độ

- [x] Bước 1: thiết kế giao thức (`docs/protocol.md`)
- [x] Bước 2: handshake TCP cơ bản (client gửi `PING`, server trả `PONG`)
- [x] Bước 3: đa luồng
- [x] Bước 4: đăng nhập và quản lý người dùng
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

## Bước 4: đăng nhập và quản lý người dùng

Client phải đăng nhập bằng gói `AUTH` trước khi làm gì khác. Server giữ list người online trong dict `clients` (username -> session), có `Lock` để các thread không giẫm chân nhau khi cùng sửa dict này.

Luật đăng nhập:
- Tên chưa có trong `server/users.json` thì tự đăng ký, mật khẩu lưu dạng hash SHA-256 (chưa salt, đơn giản hóa cho bài tập).
- Tên đã có thì so mật khẩu, sai trả `AUTH_FAIL` với lý do `Sai mật khẩu`.
- Tên đang online ở client khác trả `AUTH_FAIL` với lý do `Tên đã được dùng`.
- Tên không hợp lệ khi không phải chữ/số, dài hơn 20 ký tự, hoặc trùng `ALL` (vì `ALL` là đích của chat chung).
- Chưa đăng nhập mà gửi gói khác `AUTH` thì nhận `ERROR` với mã `NOT_AUTHENTICATED`.
- Mỗi lần có người vào hoặc ra, server broadcast `USERS` cho tất cả client đang online.

Client sau khi đăng nhập có thêm một thread riêng để nghe gói từ server, nên danh sách online tự cập nhật mà không cần gõ lệnh. Lệnh hiện có:
- `/users`: xin danh sách người online
- `/quit`: gửi `BYE` rồi thoát

Mỗi kết nối có một `send_lock` riêng (class `Session`), vì nhiều thread có thể cùng gửi vào một socket (ví dụ broadcast `USERS` trong lúc thread của client đó đang trả lời). Không có lock thì 2 gói có thể bị trộn byte vào nhau.

Cách test:
- Mở 1 terminal chạy server.
- Mở 2 terminal chạy client, đăng nhập 2 tên khác nhau. Client thứ nhất tự in ra list mới khi client thứ hai vào.
- Thử đăng nhập trùng tên, sai mật khẩu, tên `ALL` để xem `AUTH_FAIL`.
- Bấm Ctrl+C ở một client thì client kia nhận list mới không còn tên đó.

Lưu ý:
- `server/users.json` chứa hash mật khẩu nên không commit, đã thêm vào `.gitignore`.
- Mật khẩu hiện gửi plaintext trên đường truyền, bước 8 (mã hóa) sẽ xử lý.

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