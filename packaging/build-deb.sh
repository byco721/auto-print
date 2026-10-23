#!/bin/sh
# Đóng gói file .deb (Ubuntu / Debian / Linux Mint...).  Chạy:  ./packaging/build-deb.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/browserprint/__init__.py")"
PKG=browser-print
BUILD="$ROOT/build/${PKG}_${VERSION}_all"

rm -rf "$BUILD"
mkdir -p "$BUILD/DEBIAN" "$BUILD/opt/browser-print" "$BUILD/usr/bin" \
         "$BUILD/usr/share/applications" "$BUILD/usr/share/icons/hicolor/scalable/apps" \
         "$BUILD/usr/lib/systemd/user"

cp -r "$ROOT/browserprint" "$BUILD/opt/browser-print/"
find "$BUILD" -name "__pycache__" -type d -prune -exec rm -rf {} +
install -m 755 "$ROOT/packaging/browser-print" "$BUILD/usr/bin/browser-print"
install -m 644 "$ROOT/packaging/browser-print.desktop" "$BUILD/usr/share/applications/"
install -m 644 "$ROOT/packaging/browser-print.svg" "$BUILD/usr/share/icons/hicolor/scalable/apps/"
install -m 644 "$ROOT/packaging/browser-print-watch.service" "$BUILD/usr/lib/systemd/user/"

cat > "$BUILD/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-tk, python3-websocket, cups-client, xdg-utils
Recommends: cups, fonts-symbola, chromium | google-chrome-stable | microsoft-edge-stable | brave-browser
Maintainer: Browser Print <noreply@localhost>
Description: Xem trình duyệt, in chứng từ và thao tác tự động
 Kết nối Chrome/Chromium/Edge/Brave qua DevTools Protocol để in trang
 hoặc vùng chứng từ ra máy in CUPS, tự động in khi mở trang khớp quy tắc,
 và chạy macro thao tác (click, điền form, nhấn phím...).
EOF

cat > "$BUILD/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
exit 0
EOF
cat > "$BUILD/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
rm -rf /opt/browser-print/browserprint/__pycache__ 2>/dev/null || true
exit 0
EOF
chmod 755 "$BUILD/DEBIAN/postinst" "$BUILD/DEBIAN/postrm"

dpkg-deb --root-owner-group --build "$BUILD" "$ROOT/build/${PKG}_${VERSION}_all.deb"
echo "✔ Đã tạo: build/${PKG}_${VERSION}_all.deb"
echo "  Cài:  sudo apt install ./build/${PKG}_${VERSION}_all.deb"
