# test logic của client: nhận file, cập nhật danh sách online, hàm hỗ trợ giao diện

import base64
import hashlib
import os
import tempfile
import unittest

from prompt_toolkit.document import Document

import client.client as cc
from client import UI as ui_module


# giao diện giả, chỉ ghi lại những gì client muốn hiển thị để test kiểm tra
class FakeUI:

    def __init__(self):
        self.progress = {}
        self.online = frozenset()
        self.notices = []
        self.errors = []
        self.username = "phu"

    def notice(self, text):
        self.notices.append(text)

    def error(self, text):
        self.errors.append(text)


def start_packet(file_id="f1", filename="a.txt", size=11):
    return {
        "type": "FILE_START",
        "from": "nam",
        "file_id": file_id,
        "filename": filename,
        "size": size,
    }


def chunk_packet(file_id, seq, data):
    return {
        "type": "FILE_CHUNK",
        "file_id": file_id,
        "seq": seq,
        "data": base64.b64encode(data).decode(),
    }


class ReceiveFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = (cc.ui, cc.DOWNLOAD_DIR)
        cc.ui = FakeUI()
        cc.DOWNLOAD_DIR = os.path.join(self.tmp, "downloads")
        cc.incoming.clear()
        self.addCleanup(self.restore)

    def restore(self):
        cc.ui, cc.DOWNLOAD_DIR = self.old
        cc.incoming.clear()

    def receive(self, data, filename="a.txt", sha=None, parts=2):
        cc.on_file_start(start_packet("f1", filename, len(data)))
        step = max(len(data) // parts, 1)
        for seq, i in enumerate(range(0, len(data), step)):
            cc.on_file_chunk(chunk_packet("f1", seq, data[i : i + step]))
        cc.on_file_end(
            {"type": "FILE_END", "file_id": "f1", "sha256": sha or hashlib.sha256(data).hexdigest()}
        )

    def files(self):
        return sorted(os.listdir(cc.DOWNLOAD_DIR))

    def test_good_file_is_saved(self):
        self.receive(b"hello world")
        self.assertEqual(self.files(), ["a.txt"])
        with open(os.path.join(cc.DOWNLOAD_DIR, "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"hello world")
        self.assertEqual(cc.ui.progress, {})
        self.assertEqual(cc.ui.errors, [])

    def test_empty_file(self):
        self.receive(b"")
        self.assertEqual(self.files(), ["a.txt"])

    def test_wrong_sha256_is_deleted(self):
        self.receive(b"hello world", sha="0" * 64)
        self.assertEqual(self.files(), [])
        self.assertTrue(any("bị hỏng" in e for e in cc.ui.errors))

    def test_wrong_size_is_deleted(self):
        cc.on_file_start(start_packet("f1", "a.txt", 100))
        cc.on_file_chunk(chunk_packet("f1", 0, b"ngan"))
        cc.on_file_end(
            {"type": "FILE_END", "file_id": "f1", "sha256": hashlib.sha256(b"ngan").hexdigest()}
        )
        self.assertEqual(self.files(), [])

    def test_out_of_order_chunk_cancels(self):
        cc.on_file_start(start_packet("f1", "a.txt", 10))
        cc.on_file_chunk(chunk_packet("f1", 1, b"abc"))
        self.assertEqual(cc.incoming, {})
        self.assertEqual(self.files(), [])

    def test_more_data_than_declared_cancels(self):
        cc.on_file_start(start_packet("f1", "a.txt", 3))
        cc.on_file_chunk(chunk_packet("f1", 0, b"qua dai"))
        self.assertEqual(cc.incoming, {})
        self.assertEqual(self.files(), [])

    # tên file kiểu ../../evil.txt phải bị cắt chỉ còn evil.txt, không ghi ra ngoài downloads/
    def test_path_traversal_is_stripped(self):
        self.receive(b"abc", filename="../../evil.txt")
        self.assertEqual(self.files(), ["evil.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))
        self.receive(b"abc", filename="..\\..\\evil2.txt")
        self.assertIn("evil2.txt", self.files())

    def test_duplicate_names_get_a_suffix(self):
        self.receive(b"one", filename="x.txt")
        self.receive(b"two", filename="x.txt")
        self.assertEqual(self.files(), ["x (1).txt", "x.txt"])

    def test_cancel_incoming_removes_partial_file(self):
        cc.on_file_start(start_packet("f1", "a.txt", 10))
        cc.on_file_chunk(chunk_packet("f1", 0, b"abc"))
        cc.cancel_incoming("f1", "người gửi rớt")
        self.assertEqual(self.files(), [])
        self.assertEqual(cc.ui.progress, {})

    def test_progress_follows_received_bytes(self):
        cc.on_file_start(start_packet("f1", "a.txt", 10))
        cc.on_file_chunk(chunk_packet("f1", 0, b"12345"))
        self.assertEqual(cc.ui.progress["f1"]["done"], 5)
        self.assertEqual(cc.ui.progress["f1"]["total"], 10)
        cc.cancel_incoming("f1", "test")


class OnlineListTest(unittest.TestCase):
    def setUp(self):
        self.old = cc.ui
        cc.ui = FakeUI()
        self.addCleanup(lambda: setattr(cc, "ui", self.old))

    def test_first_list_then_join_and_leave_notices(self):
        cc.update_online(["nam", "phu"])
        self.assertEqual(cc.ui.notices, ["Đang online: nam, phu"])
        cc.update_online(["nam", "phu", "lam"])
        cc.update_online(["phu", "lam"])
        self.assertEqual(cc.ui.notices[1:], ["lam đã vào", "nam đã thoát"])
        self.assertEqual(cc.ui.online, frozenset({"phu", "lam"}))


class UiHelpersTest(unittest.TestCase):
    def test_format_size(self):
        self.assertEqual(ui_module.format_size(500), "500 B")
        self.assertEqual(ui_module.format_size(1536), "1.5 KB")
        self.assertEqual(ui_module.format_size(5 * 1024 * 1024), "5.0 MB")

    def test_progress_bar(self):
        self.assertEqual(ui_module.progress_bar(0, 100), "░" * 16 + " 0%")
        self.assertEqual(ui_module.progress_bar(50, 100), "█" * 8 + "░" * 8 + " 50%")
        self.assertEqual(ui_module.progress_bar(100, 100), "█" * 16 + " 100%")
        self.assertEqual(ui_module.progress_bar(0, 0), "█" * 16 + " 100%")
        self.assertTrue(ui_module.progress_bar(500, 100).endswith("100%"))

    def test_color_is_stable_per_name(self):
        self.assertEqual(ui_module.color_for("phu"), ui_module.color_for("phu"))

    def test_tab_completion(self):
        fake = FakeUI()
        fake.online = frozenset({"phu", "nam", "lam"})
        completer = ui_module.ChatCompleter(fake)

        def complete(text):
            return sorted(c.text for c in completer.get_completions(Document(text), None))

        self.assertEqual(complete("/ms"), ["/msg"])
        self.assertEqual(complete("/s"), ["/send"])
        self.assertEqual(complete("/msg n"), ["nam"])
        self.assertEqual(complete("/msg "), ["lam", "nam"])
        self.assertEqual(complete("chữ thường"), [])


if __name__ == "__main__":
    unittest.main()
