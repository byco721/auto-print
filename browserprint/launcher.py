"""Tìm và mở trình duyệt với cổng remote debugging."""

import os
import shutil
import subprocess
import time

from .cdp import Browser, CDPError

CANDIDATES = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "microsoft-edge-stable", "brave-browser", "brave",
    "vivaldi", "opera", "coccoc-browser", "com.coccoc.browser",
]


def find_browsers():
    """Trả về danh sách (tên, đường dẫn) các trình duyệt Chromium có trên máy."""
    found = []
    for name in CANDIDATES:
        path = shutil.which(name)
        if path and path not in [p for _, p in found]:
            found.append((name, path))
    # Flatpak / Snap
    for path in ("/var/lib/flatpak/exports/bin/com.google.Chrome",
                 "/var/lib/flatpak/exports/bin/org.chromium.Chromium",
                 "/snap/bin/chromium"):
        if os.path.exists(path):
            found.append((os.path.basename(path), path))
    return found


def launch(browser_path=None, port=9222, profile_dir=None, url=None, host="127.0.0.1"):
    """Mở trình duyệt. Chrome 136+ không cho remote debugging trên hồ sơ mặc định,
    nên luôn dùng một thư mục hồ sơ riêng (đăng nhập 1 lần, lần sau giữ nguyên)."""
    br = Browser(host, port)
    if br.is_alive():
        return br
    if not browser_path:
        found = find_browsers()
        if not found:
            raise CDPError("Không tìm thấy Chrome/Chromium/Edge/Brave. Hãy cài một trình duyệt nhân Chromium.")
        browser_path = found[0][1]
    profile_dir = os.path.expanduser(profile_dir or "~/.local/share/browser-print/profile")
    os.makedirs(profile_dir, exist_ok=True)
    args = [
        browser_path,
        "--remote-debugging-port=%d" % port,
        "--remote-debugging-address=127.0.0.1",
        "--user-data-dir=%s" % profile_dir,
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if url:
        args.append(url)
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    for _ in range(50):
        if br.is_alive():
            return br
        time.sleep(0.2)
    raise CDPError("Đã mở %s nhưng không kết nối được cổng %d. Nếu trình duyệt đã mở sẵn "
                   "(không có cờ debug), hãy đóng hết cửa sổ rồi thử lại." % (browser_path, port))
