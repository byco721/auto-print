#!/bin/sh
# Cài đặt Browser Print trên Linux.  Chạy:  sudo ./install.sh
set -e

PREFIX_DIR=/opt/browser-print
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Cần quyền root. Chạy lại:  sudo $0"
    exit 1
fi

echo "==> Cài các gói phụ thuộc (python3, tkinter, websocket-client, CUPS)"
if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3 python3-tk python3-websocket cups cups-client xdg-utils
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-tkinter python3-websocket-client cups cups-client xdg-utils
elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install python3 python3-tk python3-websocket-client cups cups-client xdg-utils
elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm --needed python tk python-websocket-client cups xdg-utils
else
    echo "!! Không nhận ra trình quản lý gói. Hãy tự cài: python3, tkinter, websocket-client, cups"
fi

if ! python3 -c "import websocket" 2>/dev/null; then
    echo "==> Cài websocket-client bằng pip"
    python3 -m pip install --break-system-packages websocket-client 2>/dev/null \
        || python3 -m pip install websocket-client
fi

echo "==> Chép chương trình vào $PREFIX_DIR"
rm -rf "$PREFIX_DIR"
mkdir -p "$PREFIX_DIR"
cp -r "$SRC_DIR/browserprint" "$PREFIX_DIR/"
find "$PREFIX_DIR" -name "__pycache__" -type d -prune -exec rm -rf {} +
install -m 755 "$SRC_DIR/packaging/browser-print" /usr/bin/browser-print
install -m 644 "$SRC_DIR/packaging/browser-print.desktop" /usr/share/applications/browser-print.desktop
install -D -m 644 "$SRC_DIR/packaging/browser-print.svg" /usr/share/icons/hicolor/scalable/apps/browser-print.svg
install -D -m 644 "$SRC_DIR/packaging/browser-print-watch.service" /usr/lib/systemd/user/browser-print-watch.service

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
command -v systemctl >/dev/null 2>&1 && systemctl enable --now cups >/dev/null 2>&1 || true

echo
echo "✔ Đã cài xong. Mở từ menu ứng dụng: 'Browser Print', hoặc chạy lệnh:  browser-print"
echo "  Tự in chạy nền khi đăng nhập (tùy chọn):  systemctl --user enable --now browser-print-watch"
