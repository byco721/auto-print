#!/bin/sh
# Gỡ Browser Print. Cấu hình người dùng (~/.config/browser-print) được giữ lại.
set -e
if [ "$(id -u)" -ne 0 ]; then
    echo "Cần quyền root. Chạy lại:  sudo $0"
    exit 1
fi
rm -rf /opt/browser-print
rm -f /usr/bin/browser-print /usr/share/applications/browser-print.desktop \
      /usr/share/icons/hicolor/scalable/apps/browser-print.svg \
      /usr/lib/systemd/user/browser-print-watch.service
echo "✔ Đã gỡ Browser Print."
