# Browser Print — In chứng từ từ trình duyệt (Linux)

Ứng dụng desktop cho Linux, kết nối vào trình duyệt để:

- **Xem** các tab đang mở (tiêu đề, địa chỉ) — tự làm mới 3 giây/lần.
- **In chứng từ**: in cả trang hoặc **chỉ vùng chứng từ** (bỏ menu, thanh công cụ), chọn máy in, số bản, khổ giấy (A4, A5, K80, K58...), in ngang, lề. Có xem trước và lưu bản PDF để tra cứu lại.
- **Tự động in**: khi một tab mở tới trang khớp quy tắc (vd. `/phieu-thu/\d+`), app tự in ngay, mỗi chứng từ một lần.
- **Thao tác trên trình duyệt**: mở URL, click, điền form, chọn danh sách, gõ chữ, nhấn phím, lấy nội dung, chạy JavaScript, chụp màn hình.
- **Macro**: chuỗi thao tác dạng JSON có biến (vd. tìm số hóa đơn → mở → in 2 bản).
- **Chọn vùng bằng chuột** 🎯: click vào vùng trên trang, app tự tạo CSS selector.

Hỗ trợ trình duyệt nhân Chromium: **Google Chrome, Chromium, Microsoft Edge, Brave, Vivaldi, Opera, Cốc Cốc**. (Firefox chưa hỗ trợ.)
In qua **CUPS** nên dùng được mọi máy in đã cài trên Linux.

## Cài đặt

### Cách 1 — gói .deb (Ubuntu, Debian, Linux Mint, Zorin...)

```bash
./packaging/build-deb.sh
```

```bash
sudo apt install ./build/browser-print_1.0.0_all.deb
```

### Cách 2 — script cài (mọi bản phân phối: Debian/Ubuntu, Fedora, openSUSE, Arch)

```bash
chmod +x install.sh && sudo ./install.sh
```

Gỡ cài đặt: `sudo ./uninstall.sh` (hoặc `sudo apt remove browser-print`).

### Chạy thử không cần cài

```bash
sudo apt install python3-tk python3-websocket cups-client
```

```bash
python3 -m browserprint
```

## Sử dụng

1. Mở app **Browser Print** từ menu ứng dụng (hoặc lệnh `browser-print`).
2. Bấm **🚀 Mở trình duyệt**. Trình duyệt mở với hồ sơ riêng của app — đăng nhập phần mềm kế toán/ERP/web bán hàng một lần, lần sau vẫn giữ.
   > Chrome bản mới không cho điều khiển hồ sơ mặc định, nên app luôn dùng hồ sơ riêng ở `~/.local/share/browser-print/profile`.
   > Nếu muốn tự mở trình duyệt: `google-chrome --remote-debugging-port=9222 --user-data-dir=$HOME/.local/share/browser-print/profile`
3. Chọn tab ở danh sách bên trái, sang thẻ **🖨 In chứng từ** → chọn máy in → **IN NGAY**.
4. Để chỉ in phần chứng từ: bấm **🎯 Chọn bằng chuột**, chuyển sang trình duyệt và click vào khung phiếu.

### Tự động in

Thẻ **⚡ Tự động in** → **Từ tab đang chọn** (app tự tạo quy tắc từ địa chỉ trang, thay các số bằng `\d+`) → Lưu → **BẮT ĐẦU THEO DÕI**.

| Trường | Ý nghĩa |
|---|---|
| URL khớp | Biểu thức chính quy, vd `ketoan\.congty\.vn/phieu-thu/\d+` |
| Tiêu đề khớp | (tùy chọn) vd `Hóa đơn` |
| Vùng in | CSS selector, trống = cả trang |
| Chờ phần tử | chờ phần tử này hiện ra rồi mới in (trang tải dữ liệu chậm) |
| Đóng tab sau khi in | dùng cho các trang "in phiếu" mở ra tab mới |

Các tab đã mở sẵn khi bắt đầu theo dõi sẽ **không** bị in lại. Chạy nền khi đăng nhập (không cần mở giao diện):

```bash
systemctl --user enable --now browser-print-watch
```

### Web tạo chứng từ PDF ở máy chủ (MISA, hóa đơn điện tử, ngân hàng...)

Nhiều phần mềm (MISA AMIS/meInvoice, hóa đơn điện tử, ngân hàng...) **tạo file PDF ở máy chủ** rồi mới gửi về trình duyệt. Với các trang này, nếu chụp lại trang hiển thị thành PDF thì thường ra trang trắng hoặc thiếu trang. Vì vậy app **in đúng file PDF gốc** máy chủ gửi về, theo 2 cách:

**1. Tự động — nút "IN NGAY" và quy tắc tự in.** Nếu tab đang mở trình xem PDF, hoặc trang có PDF nhúng trong `iframe/embed/object`, app tự tải lại file gốc ngay trong phiên đăng nhập của tab và gửi thẳng tới máy in, không cần cài đặt gì thêm.

**2. Thẻ "📥 PDF máy chủ" — bắt PDF trên đường truyền.** Bấm **BẮT ĐẦU BẮT PDF**. Từ lúc đó, mọi file PDF máy chủ gửi về đều được lưu đúng bản gốc vào `~/.local/share/browser-print/pdf/server/` và có thể tự in ngay. Cách này bắt được mọi kiểu trả file:

| Web trả chứng từ kiểu | Cách 1 (tải lại) | Cách 2 (bắt PDF) |
|---|---|---|
| Mở PDF trong tab mới (GET) | ✔ | ✔ |
| Gửi form **POST** → tab mới hiện PDF | ✖ | ✔ |
| Link **dùng một lần** / có token hết hạn | ✖ | ✔ |
| Nút **Tải về** (attachment) | — | ✔ |
| Tải ngầm qua XHR/fetch rồi xem bằng `blob:` | ✖ | ✔ |
| PDF nhúng trong iframe của trang | ✔ | ✔ |

Lưu ý:
- Ô **"Chỉ bắt khi URL/trang khớp"** (vd `misa|meinvoice`) giúp tránh in nhầm các PDF khác.
- Đã bật bắt PDF thì nút "IN NGAY" vẫn in được cả các chứng từ không tải lại được (POST, link một lần) vì dùng bản vừa bắt.
- Không bật cùng lúc **tự động in** ở thẻ "PDF máy chủ" **và** một quy tắc ở thẻ "Tự động in" cho cùng một trang, nếu không sẽ in 2 lần.
- PDF gốc được in với tùy chọn CUPS `fit-to-page` (co giãn vừa khổ giấy máy in). Có thể tắt tùy chọn này ở thẻ In chứng từ.
- Có thể kết hợp với macro: macro bấm nút "In/Xuất PDF" trên web, bộ bắt PDF nhận file và tự in.

### 🏷 In theo điều kiện + gắn thẻ "Đã in"

Thẻ này quét **danh sách chứng từ** đang mở trên web, **chỉ in những dòng thỏa điều kiện**, rồi **gắn thẻ "Đã in"** lên chứng từ sau khi máy in đã in xong. Mặc định đã cấu hình sẵn:

- In khi cột **Tình trạng ghi doanh số** là **Đề nghị ghi** hoặc **Đã ghi**.
- Bỏ qua dòng đã có thẻ **Đã in** (đọc từ cột **Thẻ**).

**Thiết lập một lần:**
1. Mở trang danh sách chứng từ trên web, chọn tab đó trong app, bấm **Lấy từ tab** để điền URL danh sách.
2. **Dòng danh sách**: bấm 🎯 rồi click vào **một dòng bất kỳ**. App tự tạo bộ chọn khớp mọi dòng cùng loại.
3. Kiểm tra tên các cột: *Cột điều kiện*, *Cột mã chứng từ* (ví dụ `Số đơn hàng`, nên có để chống in trùng chính xác), *Cột thẻ*. App tìm cột **theo tên tiêu đề**, nên không phụ thuộc thứ tự cột.
4. **Bước bấm In**: bấm **📹 Ghi thao tác**, sang trình duyệt bấm nút In của **một** dòng, rồi bấm **Dừng ghi**.
5. **Bước gắn thẻ**: bấm **📹 Ghi thao tác**, rồi trên trình duyệt làm đúng các bước gắn thẻ cho một dòng (tích dòng → *Gắn thẻ* → *Đã in* → *Đồng ý*), rồi **Dừng ghi**.
   > Các bước trong dòng được ghi kèm `"scope": "row"`, nên khi chạy sẽ áp lần lượt cho từng dòng. Nút có chữ cố định được ghi bằng chữ (`click_text`), không phụ thuộc giao diện thay đổi.
6. Nếu danh sách có nhiều trang: bấm 🎯 ở **Nút trang sau** rồi click vào nút "Trang sau".
7. **🔍 Chạy thử**: xem bảng kết quả từng chứng từ "sẽ in" hay "bỏ qua — vì sao", **không in gì**.
8. **▶ CHẠY**. Có thể đặt **Tự chạy lại mỗi N phút** để app tự in các chứng từ mới được ghi doanh số.

**MISA AMIS CRM (đã khảo sát trên trang Đơn hàng thật):** chọn **Mẫu có sẵn → MISA AMIS CRM — Đơn hàng**. Mẫu này đã điền sẵn:
- cột `Tình trạng ghi DS`, cột mã `Số đơn hàng`, cột thẻ `Thẻ`;
- dòng danh sách `table.jsgrid-table.JColResizer > tbody > tr`;
- các bước gắn thẻ: *Bỏ chọn* → tích dòng → *Gắn thẻ* → nhập `Đã in` → *Lưu*.

Bước bấm In cũng đã có sẵn, dựng theo hộp thoại thật: *Bỏ chọn* → tích dòng → **Trộn mẫu** → click tên mẫu **IN A5_032026** → `wait_pdf` → đóng hộp xem trước → đóng hộp *Chọn mẫu*.
- MISA tạo PDF ở máy chủ (`…/doctemplate/download/file?token=…`). App **lấy thẳng file PDF đó để in** và **không bấm nút "In" của MISA**, vì nút này mở hộp thoại in của trình duyệt và sẽ làm treo quy trình.
- Nếu công ty dùng mẫu khác, hãy sửa chữ `IN A5_032026` trong *Bước bấm In* thành đúng tên mẫu, ví dụ `pxk long chau`.
- `{"action": "wait_pdf"}` chia *Bước bấm In* làm hai: các bước trước nó mở bản in, các bước sau nó chạy sau khi đã in (đóng hộp thoại).

Lưu ý:
- Quy trình luôn **bấm "Bỏ chọn" trước** khi tích dòng, vì MISA gắn thẻ cho **mọi** dòng đang được tích.
- **Không** tích ô "Thay thế các thẻ đã gắn…", vì ô này xóa các thẻ cũ của đơn.
- Bước có `"optional": true` sẽ được bỏ qua nếu không tìm thấy (ví dụ không có dòng nào đang chọn thì không có nút "Bỏ chọn").

**Cơ chế an toàn:**
- Chỉ gắn thẻ **sau khi CUPS báo lệnh in đã xong**. Máy in hết giấy, kẹt giấy hoặc tắt thì không gắn thẻ và báo cảnh báo.
- **Sổ đã in** (`~/.local/share/browser-print/printed.json`) ghi mọi chứng từ đã gửi in. Nếu gắn thẻ bị lỗi, lần chạy sau **chỉ gắn thẻ lại, không in lại**.
- Sau khi gắn thẻ, app đọc lại dòng để kiểm tra thẻ đã hiện ra.
- Trong lúc quy trình chạy, chức năng tự in của thẻ "PDF máy chủ" tạm dừng để không in 2 lần.
- Dùng được cả khi web mở trang in HTML thay vì PDF: app in trang đó rồi đóng tab.

Chạy từ dòng lệnh hoặc dịch vụ nền (dùng quy trình đã **💾 Lưu** trong giao diện):

```text
browser-print workflow -u /don-hang --dry-run     # chạy thử trên tab có URL chứa /don-hang
browser-print workflow -u /don-hang --every 10    # in + gắn thẻ, lặp lại mỗi 10 phút
```

### Macro

```json
{
  "variables": {"so_hd": "HD0001"},
  "steps": [
    {"action": "goto", "url": "https://erp.congty.vn/invoices"},
    {"action": "fill", "selector": "#search", "value": "{{so_hd}}"},
    {"action": "press", "key": "Enter"},
    {"action": "wait_for", "selector": "#invoice", "timeout": 20},
    {"action": "print", "selector": "#invoice", "copies": 2, "paper": "A5"}
  ]
}
```

Hành động: `goto, reload, click, click_text, wait_text, fill, select, check, focus, type, press, wait, wait_for, wait_load, eval, get_text, screenshot, print, pdf, log`. `click_text` bấm theo chữ hiển thị (vd `{"action": "click_text", "text": "Gắn thẻ"}`) và ưu tiên tìm trong hộp thoại đang mở. Bấm **Hướng dẫn** trong thẻ Macro để xem tham số.

## Dòng lệnh

```text
browser-print                       # mở giao diện
browser-print launch [url]          # mở trình duyệt có cổng điều khiển
browser-print tabs                  # liệt kê tab
browser-print printers              # liệt kê máy in (* = mặc định)
browser-print print -u phieu-thu -s "#invoice" -P HP_LaserJet -n 2 --paper A5
browser-print print -i 0 --pdf ~/phieu.pdf        # chỉ lưu PDF
browser-print macro in_hd.json -v so_hd=HD0002
browser-print watch                 # tự in theo quy tắc (chạy nền)
browser-print watch --capture       # ... và bắt/in PDF do máy chủ tạo
browser-print capture -f "misa" -P HP_LaserJet -n 2   # chỉ bắt & in PDF máy chủ
```

Muốn dịch vụ chạy nền `browser-print-watch` cũng bắt PDF: tích "Tự bật khi mở ứng dụng" ở thẻ PDF máy chủ (lưu `capture.enabled=true` trong cấu hình).

## Xử lý sự cố

**App tắt ngay khi mở, báo `X Error of failed request: BadLength … RenderAddGlyphs`.** Lỗi này do Tk 8.6 trên Linux không vẽ được emoji bằng font màu (Noto Color Emoji).
- Từ bản này, app tự cho thư viện font bỏ qua font emoji màu, nên không còn bị.
- Để các biểu tượng hiện đẹp, nên cài thêm font emoji đơn sắc:

```bash
sudo apt install fonts-symbola
```

Nếu muốn tắt cơ chế này (ví dụ đã có Tk 8.7 trở lên), đặt biến môi trường `BROWSER_PRINT_KEEP_FONTCONFIG=1`.

## Tệp tin

- Cấu hình & quy tắc: `~/.config/browser-print/config.json`
- Macro: `~/.config/browser-print/macros/`
- Bản PDF đã in: `~/.local/share/browser-print/pdf/`

## Bảo mật

Cổng điều khiển chỉ mở trên `127.0.0.1` (máy tính hiện tại). Bất kỳ chương trình nào trên máy cũng có thể điều khiển trình duyệt qua cổng này khi nó đang mở, vì vậy hãy dùng hồ sơ trình duyệt riêng của app cho công việc in chứng từ.

## Cấu trúc mã nguồn

```text
browserprint/
  cdp.py       điều khiển trình duyệt qua Chrome DevTools Protocol
  launcher.py  tìm & mở trình duyệt
  printing.py  in qua CUPS (lp, lpstat)
  jobs.py      xuất PDF + gửi máy in
  watcher.py   theo dõi tab, tự in theo quy tắc
  capture.py   bắt PDF do máy chủ tạo (CDP Fetch, tự gắn vào mọi tab/iframe)
  workflow.py  in theo điều kiện trên danh sách + gắn thẻ + sổ chống in trùng
  macro.py     chạy kịch bản thao tác
  gui.py       giao diện Tkinter
  cli.py       dòng lệnh
packaging/     .deb, lối tắt menu, biểu tượng, dịch vụ systemd
```
