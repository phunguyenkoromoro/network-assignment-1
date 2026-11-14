from datetime import datetime

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import clear, print_formatted_text
from prompt_toolkit.styles import Style

COMMANDS = ["/msg", "/send", "/users", "/help", "/clear", "/quit"]
HELP_LINES = [
    "Gõ chữ bình thường rồi Enter để chat chung. Bấm Tab để gợi ý lệnh và tên người dùng.",
    "  /msg <tên> <nội dung>    nhắn riêng",
    "  /send <tên> <đường dẫn>  gửi file",
    "  /users                   xem người đang online",
    "  /clear                   xóa màn hình",
    "  /quit                    thoát (hoặc Ctrl+C, Ctrl+D)",
]
COLORS = ["ansicyan", "ansigreen", "ansiyellow", "ansimagenta", "ansiblue"]
STYLE = Style.from_dict({
    "time": "ansibrightblack",
    "notice": "ansibrightblack",
    "error": "ansired bold",
    "me": "ansigreen bold",
    "private": "ansimagenta bold",
    "prompt": "ansigreen bold",
})


def format_size(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def progress_bar(done, total, width=16):
    ratio = 1.0 if total == 0 else min(done / total, 1.0)
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled) + f" {int(ratio * 100)}%"


def color_for(name):
    return COLORS[sum(map(ord, name)) % len(COLORS)]


class ChatCompleter(Completer):
    def __init__(self, ui):
        self.ui = ui

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if " " not in text:
            if text.startswith("/"):
                for command in COMMANDS:
                    if command.startswith(text):
                        yield Completion(command, start_position=-len(text))
            return
        command, _, rest = text.partition(" ")
        if command in ("/msg", "/send") and " " not in rest:
            for name in sorted(self.ui.online):
                if name != self.ui.username and name.startswith(rest):
                    yield Completion(name, start_position=-len(rest))


class ChatUI:
    def __init__(self, username):
        self.username = username
        self.online = frozenset()
        self.progress = {}
        self.session = PromptSession(
            completer=ChatCompleter(self),
            bottom_toolbar=self.toolbar,
            refresh_interval=0.3,
            style=STYLE,
        )

    def toolbar(self):
        names = ", ".join(sorted(self.online)) or "-"
        text = f" Online ({len(self.online)}): {names} "
        for t in list(self.progress.values()):
            arrow = "↑ gửi" if t["dir"] == "send" else "↓ nhận"
            text += f"| {arrow} {t['name']} {progress_bar(t['done'], t['total'])} "
        return text

    def context(self):
        return patch_stdout()

    def read(self):
        return self.session.prompt([("class:prompt", f"{self.username}> ")])

    def interrupt(self):
        app = self.session.app
        if app.is_running:
            app.loop.call_soon_threadsafe(lambda: app.exit(exception=EOFError()))

    def clear(self):
        clear()

    def show(self, *parts):
        stamp = datetime.now().strftime("%H:%M")
        print_formatted_text(FormattedText([("class:time", f"[{stamp}] "), *parts]), style=STYLE)

    def chat(self, sender, text):
        self.show((f"{color_for(sender)} bold", f"{sender}: "), ("", text))

    def own_chat(self, text):
        self.show(("class:me", "bạn: "), ("", text))

    def private_in(self, sender, text):
        self.show(("class:private", f"{sender} → bạn (riêng): "), ("", text))

    def private_out(self, to, text):
        self.show(("class:me", f"bạn → {to} (riêng): "), ("", text))

    def notice(self, text):
        self.show(("class:notice", f"*** {text}"))

    def error(self, text):
        self.show(("class:error", f"Lỗi: {text}"))

    def users(self):
        self.notice(f"Đang online ({len(self.online)}): " + ", ".join(sorted(self.online)))

    def help(self):
        for line in HELP_LINES:
            print_formatted_text(FormattedText([("class:notice", line)]), style=STYLE)
