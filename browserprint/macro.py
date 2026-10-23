"""Kịch bản thao tác (macro) dạng JSON.

Ví dụ:
[
  {"action": "goto", "url": "https://erp.example.com/invoices"},
  {"action": "fill", "selector": "#search", "value": "{{so_hd}}"},
  {"action": "press", "key": "Enter"},
  {"action": "wait_for", "selector": "#invoice"},
  {"action": "print", "selector": "#invoice", "copies": 2, "paper": "A5"}
]

Biến {{ten}} được thay bằng giá trị truyền vào khi chạy.
"""

import json
import os
import re
import time

from .cdp import CDPError
from .jobs import print_tab

ACTIONS = {
    "goto": "Mở URL (url)",
    "reload": "Tải lại trang",
    "click": "Click phần tử (selector)",
    "click_text": "Click nút/mục theo chữ hiển thị (text, exact, timeout)",
    "wait_text": "Chờ chữ xuất hiện (text, timeout)",
    "fill": "Điền ô nhập (selector, value)",
    "select": "Chọn trong danh sách (selector, value)",
    "check": "Đánh dấu ô (selector, checked=true/false)",
    "focus": "Focus phần tử (selector)",
    "type": "Gõ chữ vào ô đang focus (text)",
    "press": "Nhấn phím (key: Enter, Tab, Escape...)",
    "wait": "Chờ (seconds)",
    "wait_for": "Chờ phần tử xuất hiện (selector, timeout)",
    "wait_load": "Chờ trang tải xong",
    "eval": "Chạy JavaScript (script, save_as)",
    "get_text": "Lấy chữ của phần tử vào biến (selector, save_as)",
    "screenshot": "Chụp màn hình (path, full_page)",
    "print": "In (selector, printer, copies, paper, landscape)",
    "wait_pdf": "(In theo điều kiện) chờ PDF máy chủ trả về rồi in; các bước sau nó chạy sau khi in",
    "pdf": "Lưu PDF (path, selector, paper, landscape)",
    "log": "Ghi nhật ký (text)",
}

EXAMPLE = [
    {"action": "goto", "url": "https://example.com"},
    {"action": "wait_for", "selector": "h1"},
    {"action": "log", "text": "Tiêu đề: {{title}}"},
    {"action": "print", "selector": "body", "copies": 1, "paper": "A4"},
]


def _subst(value, variables):
    if isinstance(value, str):
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(variables.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: _subst(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, variables) for v in value]
    return value


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, steps):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(steps, f, ensure_ascii=False, indent=2)


def run(tab, steps, cfg, variables=None, log=print, stop_event=None, row_scope=None, refind_row=None):
    """row_scope: CSS selector của dòng đang xử lý (dùng trong 'In theo điều kiện');
    bước có "scope": "row" sẽ chỉ tìm phần tử bên trong dòng đó.
    refind_row: hàm đánh dấu lại dòng trước mỗi bước "scope": "row" (danh sách hay vẽ lại)."""
    if isinstance(steps, dict):
        variables = dict(steps.get("variables", {}), **(variables or {}))
        steps = steps.get("steps", [])
    variables = dict(variables or {})
    for i, raw in enumerate(steps, 1):
        if stop_event is not None and stop_event.is_set():
            log("■ Đã dừng macro")
            return
        variables.setdefault("title", "")
        try:
            variables["title"] = tab.title() or ""
            variables["url"] = tab.url() or ""
        except CDPError:
            pass
        s = _subst(raw, variables)
        act = s.get("action")
        sel = s.get("selector", "")
        scope = None
        if s.get("scope") == "row":
            if not row_scope:
                raise CDPError("Bước %d dùng scope=row nhưng không có dòng đang xử lý" % i)
            if refind_row:
                refind_row()
            scope = row_scope
            if sel:
                sel = row_scope if sel == ":scope" else "%s %s" % (row_scope, sel)
        log("  %d. %s %s" % (i, act, sel or s.get("text", "") or s.get("url", "") or s.get("key", "")))
        try:
            _step(tab, s, act, sel, scope, cfg, variables, log, i)
        except CDPError as e:
            if not s.get("optional"):
                raise
            log("     (bỏ qua bước không bắt buộc: %s)" % e)
    log("✔ Macro hoàn tất")


def _step(tab, s, act, sel, scope, cfg, variables, log, i):
    if act == "goto":
        tab.navigate(s["url"], timeout=s.get("timeout", 30))
    elif act == "reload":
        tab.reload()
    elif act == "click":
        tab.click(sel)
    elif act == "click_text":
        tab.click_text(str(s.get("text", "")), float(s.get("timeout", 15)), scope, s.get("exact", False))
    elif act == "wait_text":
        tab.wait_text(str(s.get("text", "")), float(s.get("timeout", 15)), scope, s.get("exact", False))
    elif act == "fill":
        tab.fill(sel, str(s.get("value", "")))
    elif act == "select":
        tab.select(sel, str(s.get("value", "")))
    elif act == "check":
        tab.check(sel, s.get("checked", True))
    elif act == "focus":
        tab.focus(sel)
    elif act == "type":
        tab.type_text(str(s.get("text", "")))
    elif act == "press":
        tab.press(s.get("key", "Enter"))
    elif act == "wait":
        time.sleep(float(s.get("seconds", 1)))
    elif act == "wait_for":
        tab.wait_for_selector(sel, float(s.get("timeout", 30)), visible=s.get("visible", False))
    elif act == "wait_load":
        tab.wait_for_load(float(s.get("timeout", 30)))
    elif act == "eval":
        res = tab.evaluate(s.get("script", ""), await_promise=True)
        if s.get("save_as"):
            variables[s["save_as"]] = res
        log("     → %r" % (res,))
    elif act == "get_text":
        res = tab.get_text(sel)
        variables[s.get("save_as", "text")] = res
        log("     → %r" % (res,))
    elif act == "screenshot":
        path = os.path.expanduser(s.get("path") or "~/screenshot-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
        tab.screenshot(path, s.get("full_page", False))
        log("     → %s" % path)
    elif act in ("print", "pdf"):
        pdf, job = print_tab(
            tab,
            printer=s.get("printer") or cfg.get("printer"),
            copies=s.get("copies", cfg.get("copies", 1)),
            paper=s.get("paper", cfg.get("paper", "A4")),
            landscape=s.get("landscape", cfg.get("landscape", False)),
            selector=sel or None,
            margin_mm=s.get("margin_mm", cfg.get("margin_mm", 10)),
            keep_pdf=True if act == "pdf" else cfg.get("keep_pdf", True),
            pdf_path=os.path.expanduser(s["path"]) if s.get("path") else None,
            send_to_printer=(act == "print"),
            fit_to_page=cfg.get("fit_to_page", True),
            log=log,
        )
        log("     → %s %s" % (pdf, ("(lệnh in %s)" % job) if job else ""))
    elif act == "wait_pdf":
        raise CDPError("Bước %d: 'wait_pdf' chỉ dùng trong 'Bước bấm In' của In theo điều kiện" % i)
    elif act == "log":
        log("     " + str(s.get("text", "")))
    else:
        raise CDPError("Bước %d: hành động không hợp lệ '%s'" % (i, act))
