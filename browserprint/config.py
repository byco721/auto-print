"""Lưu/đọc cấu hình tại ~/.config/browser-print/config.json"""

import copy
import json
import os

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "browser-print")
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "browser-print")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
MACRO_DIR = os.path.join(CONFIG_DIR, "macros")
PDF_DIR = os.path.join(DATA_DIR, "pdf")

DEFAULT_RULE = {
    "name": "Quy tắc mới",
    "enabled": True,
    "url_pattern": "",        # biểu thức chính quy khớp URL, vd: /invoice/\d+
    "title_pattern": "",      # (tùy chọn) khớp tiêu đề tab
    "selector": "",           # (tùy chọn) chỉ in vùng này, vd: #print-area
    "wait_selector": "",      # (tùy chọn) chờ phần tử này xuất hiện rồi mới in
    "delay": 1.0,             # chờ thêm (giây) sau khi trang tải xong
    "printer": "",            # trống = máy in mặc định
    "copies": 1,
    "paper": "A4",
    "landscape": False,
    "close_tab": False,       # đóng tab sau khi in
}

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 9222,
    "browser_path": "",
    "profile_dir": os.path.join(DATA_DIR, "profile"),
    "printer": "",
    "copies": 1,
    "paper": "A4",
    "landscape": False,
    "margin_mm": 10,
    "keep_pdf": True,
    "fit_to_page": True,      # co giãn PDF gốc của máy chủ vừa khổ giấy máy in
    "capture": {              # bắt PDF do máy chủ tạo (xem capture.py)
        "enabled": False,     # tự bật khi mở ứng dụng / chạy watch
        "auto_print": True,
        "url_pattern": "",    # chỉ bắt PDF có URL hoặc trang khớp regex này (trống = mọi PDF)
        "include_xhr": True,  # bắt cả PDF lấy qua XHR/fetch (hiển thị bằng blob:)
    },
    "watch_interval": 2.0,
    "watch_on_start": False,
    "rules": [],
}


def load():
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    cfg["rules"] = [dict(DEFAULT_RULE, **r) for r in cfg.get("rules", [])]
    cfg["capture"] = dict(DEFAULTS["capture"], **cfg.get("capture", {}))
    return cfg


def save(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


def ensure_dirs():
    for d in (CONFIG_DIR, MACRO_DIR, PDF_DIR):
        os.makedirs(d, exist_ok=True)
