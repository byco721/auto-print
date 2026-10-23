"""Tác vụ in dùng chung cho GUI, CLI, bộ theo dõi và macro."""

import os
import re
import tempfile
import time

from . import capture, printing
from .cdp import CDPError
from .config import PDF_DIR


def safe_name(text, limit=60):
    text = re.sub(r"[^\w\-. ]+", "_", text or "", flags=re.UNICODE).strip(" ._")
    return (text or "trang")[:limit]


def _out_path(title, keep_pdf, pdf_path):
    """Trả về (đường dẫn, là file tạm?)"""
    if pdf_path:
        return pdf_path, False
    if keep_pdf:
        os.makedirs(PDF_DIR, exist_ok=True)
        return os.path.join(PDF_DIR, "%s_%s.pdf" % (time.strftime("%Y%m%d-%H%M%S"), safe_name(title))), False
    fd, path = tempfile.mkstemp(suffix=".pdf", prefix="browser-print-")
    os.close(fd)
    return path, True


def original_pdf(tab, src):
    """Lấy file PDF gốc mà máy chủ đã tạo cho tab này."""
    try:
        data = tab.fetch_bytes(src)
        if data[:5] == b"%PDF-":
            return data
        err = "nội dung tải về không phải PDF"
    except CDPError as e:
        err = e
    # Không tải lại được (form POST, link dùng một lần...): dùng bản đã bắt từ mạng
    for url in (src, tab.url()):
        path = capture.recent_for(url)
        if path:
            with open(path, "rb") as f:
                return f.read()
    raise CDPError("Trang hiển thị PDF do máy chủ tạo nhưng không tải lại được (%s). "
                   "Hãy bật '📥 Bắt PDF từ máy chủ' rồi mở lại chứng từ." % err)


def print_tab(tab, printer=None, copies=1, paper="A4", landscape=False, selector=None,
              margin_mm=10, keep_pdf=True, pdf_path=None, send_to_printer=True, title=None,
              fit_to_page=True, log=None):
    """In tab. Nếu tab đang hiển thị PDF do máy chủ tạo thì in đúng file PDF gốc,
    ngược lại dựng trang thành PDF. Trả về (đường dẫn pdf, job id)."""
    title = title or tab.title() or "trang"
    src = None
    try:
        src = tab.pdf_source(allow_embedded=not selector)
    except CDPError:
        pass
    path, is_temp = _out_path(title, keep_pdf, pdf_path)
    options = {}
    if src:
        if log:
            log("📄 Trang là PDF do máy chủ tạo — in file gốc: %s" % src[:100])
        with open(path, "wb") as f:
            f.write(original_pdf(tab, src))
        if fit_to_page:
            options["fit-to-page"] = True
    else:
        tab.print_pdf(path, paper=paper, landscape=landscape, selector=selector or None,
                      margin_mm=margin_mm)
    job = None
    if send_to_printer:
        try:
            job = printing.print_file(path, printer=printer or None, copies=copies, title=title,
                                      options=options)
        finally:
            if is_temp:
                # lp đã đọc xong file khi lệnh trả về
                try:
                    os.remove(path)
                except OSError:
                    pass
    return path, job
