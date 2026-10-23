"""Giao diện Tkinter."""

import json
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__, capture, config, launcher, macro, printing, workflow
from .cdp import KEY_CODES, PAPER_SIZES, Browser, CDPError
from .jobs import print_tab
from .watcher import Watcher

# JS chọn phần tử bằng chuột: rê để tô viền, click để chọn, Esc để hủy.
PICKER_JS = r"""
(()=>{
  if (window.__bpPickerOn) return;
  window.__bpPickerOn = true; window.__bpPicked = null;
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #e11;background:rgba(255,0,0,.08);transition:all .05s';
  const tip = document.createElement('div');
  tip.style.cssText = 'position:fixed;z-index:2147483647;top:0;left:0;right:0;background:#e11;color:#fff;font:14px sans-serif;padding:6px;text-align:center';
  const ROWMODE = window.__bpPickMode === 'row';
  tip.textContent = ROWMODE ? 'Browser Print: Click vào MỘT dòng chứng từ bất kỳ trong danh sách (Esc để hủy)'
                            : 'Browser Print: Click vào vùng chứng từ cần chọn (Esc để hủy)';
  document.documentElement.append(box, tip);
  // id có chữ số thường do thư viện tự sinh (JColResizer3, dx-123...) và đổi sau mỗi lần tải -> bỏ qua
  const goodId = (el) => el.id && !/\d/.test(el.id) && document.querySelectorAll('#'+CSS.escape(el.id)).length===1;
  const cssPath = (el) => {
    if (goodId(el)) return '#'+CSS.escape(el.id);
    const parts = [];
    while (el && el.nodeType===1 && el!==document.documentElement) {
      if (goodId(el)) { parts.unshift('#'+CSS.escape(el.id)); break; }
      let s = el.tagName.toLowerCase();
      const cls = [...el.classList].filter(c=>!/(hover|active|focus|select|state|^ng-|^cdk-)/i.test(c)).slice(0,2);
      if (cls.length) s += '.' + cls.map(c=>CSS.escape(c)).join('.');
      const p = el.parentElement;
      if (p) { const sib=[...p.children].filter(c=>c.tagName===el.tagName); if (sib.length>1) s += ':nth-of-type('+(sib.indexOf(el)+1)+')'; }
      parts.unshift(s); el = p;
      const path = parts.join(' > ');   // đủ duy nhất và có class -> dừng cho ngắn gọn
      if (parts.length >= 2 && /[.#]/.test(path) && document.querySelectorAll(path).length === 1) break;
    }
    return parts.join(' > ');
  };
  const over = (e) => { const r=e.target.getBoundingClientRect(); Object.assign(box.style,{left:r.left+'px',top:r.top+'px',width:r.width+'px',height:r.height+'px'}); };
  const done = (val) => { window.__bpPicked = val; window.__bpPickerOn=false; box.remove(); tip.remove();
     document.removeEventListener('mouseover',over,true); document.removeEventListener('click',click,true); document.removeEventListener('keydown',key,true); };
  // Chế độ dòng: trả về selector khớp MỌI dòng dữ liệu cùng loại (bỏ số thứ tự,
  // chỉ giữ các class mà đa số dòng cùng cấp đều có).
  const rowSelector = (t) => {
    const row = t.closest('tr,[role=row]') || t;
    const sib = row.parentElement ? [...row.parentElement.children].filter(c => c.tagName === row.tagName) : [row];
    const common = [...row.classList].filter(c => !/(hover|active|focus|select|alt|odd|even)/i.test(c) &&
                     sib.filter(s => s.classList.contains(c)).length >= sib.length * 0.6).slice(0, 2);
    let s = row.tagName.toLowerCase() + common.map(c => '.' + CSS.escape(c)).join('');
    if (row.getAttribute('role') === 'row') s += '[role=row]';
    return (row.parentElement ? cssPath(row.parentElement) + ' > ' : '') + s;
  };
  const click = (e) => { e.preventDefault(); e.stopPropagation(); done(ROWMODE ? rowSelector(e.target) : cssPath(e.target)); };
  const key = (e) => { if (e.key==='Escape') done(''); };
  document.addEventListener('mouseover',over,true); document.addEventListener('click',click,true); document.addEventListener('keydown',key,true);
})()
"""

# JS ghi lại thao tác người dùng thành các bước macro. %s = selector dòng danh sách.
RECORD_JS = r"""
(()=>{
  if (window.__bpRecOn) return true;
  window.__bpRecOn = true; window.__bpRec = []; let last = 0;
  const ROW = %s;
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const seg = el => {
    if (el.id && !/\d/.test(el.id)) return '#' + CSS.escape(el.id);
    let s = el.tagName.toLowerCase();
    const cls = [...el.classList].filter(c => !/(hover|active|focus|select|state|^ng-|^cdk-|alt)/i.test(c)).slice(0, 2);
    if (cls.length) s += '.' + cls.map(c => CSS.escape(c)).join('.');
    const p = el.parentElement;
    if (p) { const sib = [...p.children].filter(c => c.tagName === el.tagName); if (sib.length > 1) s += ':nth-of-type(' + (sib.indexOf(el) + 1) + ')'; }
    return s;
  };
  const path = (el, stop) => { const parts = [];
    while (el && el.nodeType === 1 && el !== stop && el !== document.documentElement) {
      const s = seg(el); parts.unshift(s); if (s[0] === '#') break; el = el.parentElement; }
    return parts.join(' > '); };
  const onClick = e => { try {
    const now = Date.now(); if (now - last < 60) return; last = now;   // bỏ click phụ do <label>
    const el = e.target.closest('button,a,[role=button],[role=menuitem],[role=option],[role=tab],[role=checkbox],li,label,input,select,textarea') || e.target;
    const row = ROW ? el.closest(ROW) : null;
    const txt = norm(el.innerText || el.value || el.getAttribute('aria-label') || el.title);
    const isInput = el.matches('input,select,textarea,[role=checkbox]');
    const isButton = el.matches('button,a,[role=button],[role=menuitem],[role=option],[role=tab],li');
    // Trong dòng: chữ là dữ liệu của dòng (số chứng từ...) nên dùng selector, trừ nút có chữ cố định
    const useText = txt && txt.length <= 40 && !isInput && (!row || (isButton && !/\d/.test(txt)));
    const st = useText ? {action: 'click_text', text: txt}
                       : {action: 'click', selector: row ? (path(el, row) || ':scope') : path(el)};
    if (row) st.scope = 'row';
    window.__bpRec.push(st);
  } catch (_) {} };
  const onChange = e => { const t = e.target; try {
    if (t.matches('input[type=text],input[type=search],input[type=number],input:not([type]),textarea'))
      window.__bpRec.push({action: 'fill', selector: path(t), value: t.value});
    else if (t.tagName === 'SELECT')
      window.__bpRec.push({action: 'select', selector: path(t), value: t.options[t.selectedIndex].text});
  } catch (_) {} };
  document.addEventListener('click', onClick, true);
  document.addEventListener('change', onChange, true);
  window.__bpRecFlush = () => { const r = window.__bpRec; window.__bpRec = []; return r; };
  window.__bpRecStop = () => { document.removeEventListener('click', onClick, true);
    document.removeEventListener('change', onChange, true); window.__bpRecOn = false; delete window.__bpRecFlush; };
  return true;
})()
"""


class App:
    def __init__(self, root):
        self.root = root
        config.ensure_dirs()
        self.cfg = config.load()
        self.logq = queue.Queue()
        self.watcher = Watcher(self.cfg, self.log)
        self.macro_stop = threading.Event()
        self.tabs = []
        self._refreshing = False

        root.title("Browser Print %s — In chứng từ từ trình duyệt" % __version__)
        root.geometry("1150x760")
        root.minsize(900, 600)
        try:
            ttk.Style().theme_use("clam")
        except tk.TclError:
            pass
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._drain_log)
        root.after(300, self.refresh_printers)
        root.after(500, self.refresh_tabs)
        root.after(3000, self._auto_refresh)
        if self.cfg.get("watch_on_start"):
            root.after(1500, self.toggle_watch)

    # ================================================================ util
    def log(self, msg):
        self.logq.put("[%s] %s" % (time.strftime("%H:%M:%S"), msg))

    def _drain_log(self):
        try:
            while True:
                line = self.logq.get_nowait()
                self.logbox.configure(state="normal")
                self.logbox.insert("end", line + "\n")
                self.logbox.see("end")
                self.logbox.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self._drain_log)

    def bg(self, fn, done=None):
        """Chạy fn ở luồng nền để giao diện không bị treo."""
        def worker():
            try:
                res = fn()
            except (CDPError, printing.PrintError, OSError, ValueError) as e:
                self.log("✖ %s" % e)
                return
            except Exception as e:
                self.log("✖ Lỗi: %r" % e)
                return
            if done:
                self.root.after(0, lambda: done(res))
        threading.Thread(target=worker, daemon=True).start()

    def browser(self):
        self.cfg["host"] = self.var_host.get().strip() or "127.0.0.1"
        try:
            self.cfg["port"] = int(self.var_port.get())
        except ValueError:
            self.cfg["port"] = 9222
        return Browser(self.cfg["host"], self.cfg["port"])

    def selected_tab(self):
        sel = self.tree.selection()
        if sel:
            for t in self.tabs:
                if t["id"] == sel[0]:
                    return t
        if self.tabs:
            return self.tabs[0]
        raise CDPError("Chưa có tab nào. Hãy kết nối trình duyệt trước.")

    def with_tab(self, action, activate=False):
        """Kết nối tab đang chọn và chạy action(tab) ở luồng nền."""
        try:
            info = self.selected_tab()
        except CDPError as e:
            self.log("✖ %s" % e)
            return
        br = self.browser()

        def job():
            if activate:
                br.activate(info["id"])
            with br.connect(info) as tab:
                return action(tab)
        self.bg(job)

    def save_cfg(self):
        self._collect_print_opts()
        try:
            config.save(self.cfg)
        except OSError as e:
            self.log("✖ Không lưu được cấu hình: %s" % e)

    # =============================================================== build
    def _build(self):
        top = ttk.Frame(self.root, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Máy:").pack(side="left")
        self.var_host = tk.StringVar(value=self.cfg["host"])
        ttk.Entry(top, textvariable=self.var_host, width=12).pack(side="left", padx=(2, 8))
        ttk.Label(top, text="Cổng debug:").pack(side="left")
        self.var_port = tk.StringVar(value=str(self.cfg["port"]))
        ttk.Entry(top, textvariable=self.var_port, width=6).pack(side="left", padx=(2, 8))
        ttk.Button(top, text="🔌 Kết nối", command=self.refresh_tabs).pack(side="left")

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(top, text="Trình duyệt:").pack(side="left")
        self.browsers = launcher.find_browsers()
        names = [p for _, p in self.browsers]
        if self.cfg.get("browser_path") and self.cfg["browser_path"] not in names:
            names.insert(0, self.cfg["browser_path"])
        self.var_browser = tk.StringVar(value=self.cfg.get("browser_path") or (names[0] if names else ""))
        ttk.Combobox(top, textvariable=self.var_browser, values=names, width=32).pack(side="left", padx=2)
        ttk.Button(top, text="🚀 Mở trình duyệt", command=self.launch_browser).pack(side="left", padx=4)

        self.lbl_status = ttk.Label(top, text="● Chưa kết nối", foreground="#b00")
        self.lbl_status.pack(side="right")

        main = ttk.PanedWindow(self.root, orient="vertical")
        main.pack(fill="both", expand=True, padx=8)
        upper = ttk.PanedWindow(main, orient="horizontal")
        main.add(upper, weight=4)

        # ---- danh sách tab
        left = ttk.LabelFrame(upper, text="Các tab đang mở", padding=4)
        upper.add(left, weight=2)
        self.tree = ttk.Treeview(left, columns=("title", "url"), show="headings", selectmode="browse")
        self.tree.heading("title", text="Tiêu đề")
        self.tree.heading("url", text="Địa chỉ")
        self.tree.column("title", width=200)
        self.tree.column("url", width=240)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.activate_tab())
        bl = ttk.Frame(left)
        bl.pack(fill="x", pady=(4, 0))
        ttk.Button(bl, text="⟳ Làm mới", command=self.refresh_tabs).pack(side="left")
        ttk.Button(bl, text="Chuyển tới", command=self.activate_tab).pack(side="left", padx=2)
        ttk.Button(bl, text="Tab mới", command=self.new_tab).pack(side="left", padx=2)
        ttk.Button(bl, text="Đóng tab", command=self.close_tab).pack(side="left", padx=2)

        # ---- các trang chức năng
        nb = ttk.Notebook(upper)
        upper.add(nb, weight=3)
        self._build_print(nb)
        self._build_actions(nb)
        self._build_macro(nb)
        self._build_rules(nb)
        self._build_capture(nb)
        self._build_workflow(nb)

        # ---- nhật ký
        lf = ttk.LabelFrame(main, text="Nhật ký", padding=4)
        main.add(lf, weight=1)
        self.logbox = ScrolledText(lf, height=8, state="disabled", font=("monospace", 9))
        self.logbox.pack(fill="both", expand=True)

    def _selector_row(self, parent, var, row, label="Vùng in (CSS selector):"):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=var, width=36).grid(row=row, column=1, sticky="we", pady=3)
        ttk.Button(parent, text="🎯 Chọn bằng chuột",
                   command=lambda: self.pick_element(var)).grid(row=row, column=2, padx=4)

    def _build_print(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="🖨 In chứng từ")
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Máy in:").grid(row=0, column=0, sticky="w", pady=3)
        self.var_printer = tk.StringVar(value=self.cfg.get("printer", ""))
        self.cb_printer = ttk.Combobox(f, textvariable=self.var_printer, width=34)
        self.cb_printer.grid(row=0, column=1, sticky="we")
        ttk.Button(f, text="⟳", width=3, command=self.refresh_printers).grid(row=0, column=2, sticky="w", padx=4)

        ttk.Label(f, text="Số bản:").grid(row=1, column=0, sticky="w", pady=3)
        self.var_copies = tk.IntVar(value=int(self.cfg.get("copies", 1)))
        ttk.Spinbox(f, from_=1, to=99, textvariable=self.var_copies, width=6).grid(row=1, column=1, sticky="w")

        ttk.Label(f, text="Khổ giấy:").grid(row=2, column=0, sticky="w", pady=3)
        self.var_paper = tk.StringVar(value=self.cfg.get("paper", "A4"))
        ttk.Combobox(f, textvariable=self.var_paper, values=list(PAPER_SIZES), state="readonly",
                     width=22).grid(row=2, column=1, sticky="w")

        ttk.Label(f, text="Lề (mm):").grid(row=3, column=0, sticky="w", pady=3)
        self.var_margin = tk.IntVar(value=int(self.cfg.get("margin_mm", 10)))
        ttk.Spinbox(f, from_=0, to=50, textvariable=self.var_margin, width=6).grid(row=3, column=1, sticky="w")

        self.var_land = tk.BooleanVar(value=self.cfg.get("landscape", False))
        ttk.Checkbutton(f, text="In ngang (landscape)", variable=self.var_land).grid(row=4, column=1, sticky="w")
        self.var_keep = tk.BooleanVar(value=self.cfg.get("keep_pdf", True))
        ttk.Checkbutton(f, text="Lưu bản PDF để tra cứu lại", variable=self.var_keep).grid(row=5, column=1, sticky="w")
        self.var_fit = tk.BooleanVar(value=self.cfg.get("fit_to_page", True))
        ttk.Checkbutton(f, text="PDF gốc của máy chủ: co giãn vừa khổ giấy",
                        variable=self.var_fit).grid(row=5, column=2, sticky="w")

        self.var_print_sel = tk.StringVar()
        self._selector_row(f, self.var_print_sel, 6)
        ttk.Label(f, text="(để trống = in cả trang; chọn vùng để bỏ menu, thanh công cụ...)",
                  foreground="#666").grid(row=7, column=1, columnspan=2, sticky="w")

        bf = ttk.Frame(f)
        bf.grid(row=8, column=0, columnspan=3, sticky="w", pady=12)
        ttk.Button(bf, text="🖨  IN NGAY", command=self.do_print).pack(side="left", ipadx=10, ipady=4)
        ttk.Button(bf, text="👁 Xem trước", command=self.do_preview).pack(side="left", padx=6)
        ttk.Button(bf, text="💾 Lưu PDF...", command=self.do_save_pdf).pack(side="left")
        ttk.Button(bf, text="📂 Thư mục PDF", command=lambda: self.open_path(config.PDF_DIR)).pack(side="left", padx=6)
        ttk.Button(bf, text="📋 Hàng đợi in", command=self.show_jobs).pack(side="left")

    def _build_actions(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="🖱 Thao tác")
        f.columnconfigure(1, weight=1)
        self.var_act_sel = tk.StringVar()
        self._selector_row(f, self.var_act_sel, 0, "Phần tử (CSS selector):")
        ttk.Label(f, text="Giá trị / URL / chữ:").grid(row=1, column=0, sticky="w", pady=3)
        self.var_act_val = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_act_val).grid(row=1, column=1, columnspan=2, sticky="we")

        g = ttk.Frame(f)
        g.grid(row=2, column=0, columnspan=3, sticky="w", pady=8)
        def act(fn):
            # đọc ô nhập ở luồng giao diện, rồi mới chạy ở luồng nền
            return lambda: (lambda s, v: self.with_tab(lambda t: fn(t, s, v)))(
                self.var_act_sel.get().strip(), self.var_act_val.get())
        buttons = [
            ("🌐 Mở URL", act(lambda t, s, v: (t.navigate(v), self.log("✔ Đã mở %s" % v)))),
            ("👆 Click", act(lambda t, s, v: (t.click(s), self.log("✔ Đã click %s" % s)))),
            ("✏ Điền", act(lambda t, s, v: (t.fill(s, v), self.log("✔ Đã điền %s" % s)))),
            ("☰ Chọn", act(lambda t, s, v: (t.select(s, v), self.log("✔ Đã chọn '%s'" % v)))),
            ("⌨ Gõ chữ", act(lambda t, s, v: (s and t.focus(s), t.type_text(v), self.log("✔ Đã gõ")))),
            ("📄 Lấy chữ", act(lambda t, s, v: self.log("→ %r" % t.get_text(s)))),
            ("⟳ Tải lại", act(lambda t, s, v: (t.reload(), self.log("✔ Đã tải lại")))),
            ("📷 Chụp màn hình", self.do_screenshot),
        ]
        for i, (txt, cmd) in enumerate(buttons):
            ttk.Button(g, text=txt, command=cmd, width=16).grid(row=i // 4, column=i % 4, padx=2, pady=2)

        kf = ttk.Frame(f)
        kf.grid(row=3, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Label(kf, text="Nhấn phím:").pack(side="left")
        self.var_key = tk.StringVar(value="Enter")
        ttk.Combobox(kf, textvariable=self.var_key, values=list(KEY_CODES), width=12).pack(side="left", padx=4)
        ttk.Button(kf, text="Nhấn", command=lambda k=None: (lambda k: self.with_tab(
            lambda t: (t.press(k), self.log("✔ Đã nhấn %s" % k))))(self.var_key.get())).pack(side="left")

        ttk.Label(f, text="JavaScript:").grid(row=4, column=0, sticky="nw", pady=3)
        self.txt_js = tk.Text(f, height=6, font=("monospace", 9))
        self.txt_js.grid(row=4, column=1, columnspan=2, sticky="nsew")
        self.txt_js.insert("1.0", "document.title")
        f.rowconfigure(4, weight=1)
        ttk.Button(f, text="▶ Chạy JS", command=lambda: (lambda js: self.with_tab(
            lambda t: self.log("→ %r" % (t.evaluate(js, await_promise=True),))))(self.txt_js.get("1.0", "end"))
        ).grid(row=5, column=1, sticky="w", pady=4)

    def _build_macro(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="🤖 Macro")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Button(bar, text="Mẫu", command=self.macro_example).pack(side="left")
        ttk.Button(bar, text="Mở...", command=self.macro_open).pack(side="left", padx=2)
        ttk.Button(bar, text="Lưu...", command=self.macro_save).pack(side="left")
        ttk.Button(bar, text="▶ Chạy", command=self.macro_run).pack(side="left", padx=(12, 2))
        ttk.Button(bar, text="■ Dừng", command=self.macro_stop.set).pack(side="left")
        ttk.Button(bar, text="Hướng dẫn", command=self.macro_help).pack(side="right")
        vf = ttk.Frame(f)
        vf.pack(fill="x", pady=4)
        ttk.Label(vf, text="Biến (ten=giatri; ten2=giatri2):").pack(side="left")
        self.var_macro_vars = tk.StringVar()
        ttk.Entry(vf, textvariable=self.var_macro_vars).pack(side="left", fill="x", expand=True, padx=4)
        self.txt_macro = ScrolledText(f, font=("monospace", 9), undo=True)
        self.txt_macro.pack(fill="both", expand=True)
        self.macro_example()

    def _build_rules(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="⚡ Tự động in")
        ttk.Label(f, text="Khi một tab mở tới trang khớp quy tắc (vd: trang phiếu thu, hóa đơn), "
                          "ứng dụng sẽ tự in ngay.", wraplength=520, foreground="#444").pack(anchor="w")
        cols = ("on", "name", "pattern", "selector", "printer", "copies")
        self.rtree = ttk.Treeview(f, columns=cols, show="headings", height=8)
        for c, t, w in zip(cols, ("Bật", "Tên", "URL khớp", "Vùng in", "Máy in", "Bản"),
                           (40, 120, 180, 110, 110, 40)):
            self.rtree.heading(c, text=t)
            self.rtree.column(c, width=w, stretch=c in ("pattern", "name"))
        self.rtree.pack(fill="both", expand=True, pady=6)
        self.rtree.bind("<Double-1>", lambda e: self.rule_edit())
        b = ttk.Frame(f)
        b.pack(fill="x")
        ttk.Button(b, text="➕ Thêm", command=self.rule_add).pack(side="left")
        ttk.Button(b, text="Từ tab đang chọn", command=self.rule_from_tab).pack(side="left", padx=2)
        ttk.Button(b, text="Sửa", command=self.rule_edit).pack(side="left", padx=2)
        ttk.Button(b, text="Bật/Tắt", command=self.rule_toggle).pack(side="left", padx=2)
        ttk.Button(b, text="Xóa", command=self.rule_delete).pack(side="left", padx=2)

        w = ttk.Frame(f)
        w.pack(fill="x", pady=10)
        self.btn_watch = ttk.Button(w, text="▶ BẮT ĐẦU THEO DÕI", command=self.toggle_watch)
        self.btn_watch.pack(side="left", ipadx=8, ipady=3)
        self.var_watch_start = tk.BooleanVar(value=self.cfg.get("watch_on_start", False))
        ttk.Checkbutton(w, text="Tự bật khi mở ứng dụng", variable=self.var_watch_start,
                        command=self._watch_start_changed).pack(side="left", padx=10)
        self.lbl_watch = ttk.Label(w, text="Đang tắt", foreground="#b00")
        self.lbl_watch.pack(side="right")
        self.refresh_rules()

    def _build_capture(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="📥 PDF máy chủ")
        ttk.Label(f, wraplength=540, foreground="#444", text=(
            "Dành cho web tạo chứng từ PDF ở máy chủ (MISA, hóa đơn điện tử, ngân hàng...). "
            "Khi bật, mọi file PDF máy chủ gửi về trình duyệt — mở tab mới, form POST, tải về, "
            "hay hiển thị qua blob — đều được lưu đúng bản gốc và có thể tự in ngay."
        )).pack(anchor="w")
        o = ttk.Frame(f)
        o.pack(fill="x", pady=6)
        o.columnconfigure(1, weight=1)
        cap = self.cfg["capture"]
        ttk.Label(o, text="Chỉ bắt khi URL/trang khớp (regex):").grid(row=0, column=0, sticky="w")
        self.var_cap_pat = tk.StringVar(value=cap.get("url_pattern", ""))
        e = ttk.Entry(o, textvariable=self.var_cap_pat)
        e.grid(row=0, column=1, sticky="we", padx=4)
        e.bind("<FocusOut>", lambda ev: self._capture_opts())
        e.bind("<Return>", lambda ev: self._capture_opts())
        ttk.Label(o, text="vd: misa|meinvoice   (trống = mọi PDF)", foreground="#666").grid(
            row=1, column=1, sticky="w", padx=4)
        self.var_cap_auto = tk.BooleanVar(value=cap.get("auto_print", True))
        ttk.Checkbutton(o, text="Tự động in khi bắt được (dùng máy in, số bản ở thẻ In chứng từ)",
                        variable=self.var_cap_auto, command=self._capture_opts).grid(
            row=2, column=0, columnspan=2, sticky="w")
        self.var_cap_xhr = tk.BooleanVar(value=cap.get("include_xhr", True))
        ttk.Checkbutton(o, text="Bắt cả PDF tải ngầm qua XHR/fetch (xem trước dạng blob:)",
                        variable=self.var_cap_xhr, command=self._capture_opts).grid(
            row=3, column=0, columnspan=2, sticky="w")
        self.var_cap_on = tk.BooleanVar(value=cap.get("enabled", False))
        ttk.Checkbutton(o, text="Tự bật khi mở ứng dụng", variable=self.var_cap_on,
                        command=self._capture_opts).grid(row=4, column=0, columnspan=2, sticky="w")

        b = ttk.Frame(f)
        b.pack(fill="x", pady=4)
        self.btn_cap = ttk.Button(b, text="▶ BẮT ĐẦU BẮT PDF", command=self.toggle_capture)
        self.btn_cap.pack(side="left", ipadx=8, ipady=3)
        self.lbl_cap = ttk.Label(b, text="Đang tắt", foreground="#b00")
        self.lbl_cap.pack(side="right")

        cols = ("time", "name", "source", "job")
        self.ctree = ttk.Treeview(f, columns=cols, show="headings", height=6)
        for c, t, w in zip(cols, ("Giờ", "File", "Nguồn", "Lệnh in"), (60, 180, 220, 90)):
            self.ctree.heading(c, text=t)
            self.ctree.column(c, width=w, stretch=c in ("name", "source"))
        self.ctree.pack(fill="both", expand=True, pady=4)
        self.ctree.bind("<Double-1>", lambda e: self._cap_open())
        self.captured = {}
        bb = ttk.Frame(f)
        bb.pack(fill="x")
        ttk.Button(bb, text="🖨 In file đã chọn", command=self._cap_print).pack(side="left")
        ttk.Button(bb, text="👁 Mở", command=self._cap_open).pack(side="left", padx=4)
        ttk.Button(bb, text="📂 Thư mục", command=lambda: self.open_path(capture.CAPTURE_DIR)).pack(side="left")

        self.capture = capture.PdfCapture(self.cfg, self._on_captured, self.log)
        if cap.get("enabled"):
            self.root.after(1200, self.toggle_capture)

    def _capture_opts(self):
        self.cfg["capture"].update(url_pattern=self.var_cap_pat.get().strip(),
                                   auto_print=bool(self.var_cap_auto.get()),
                                   include_xhr=bool(self.var_cap_xhr.get()),
                                   enabled=bool(self.var_cap_on.get()))
        self.save_cfg()

    def toggle_capture(self):
        if self.capture.running:
            self.capture.stop()
            self.btn_cap.configure(text="▶ BẮT ĐẦU BẮT PDF")
            self.lbl_cap.configure(text="Đang tắt", foreground="#b00")
        else:
            self.browser()
            self._capture_opts()
            self.capture.start()
            self.btn_cap.configure(text="■ DỪNG BẮT PDF")
            self.lbl_cap.configure(text="● Đang bắt PDF", foreground="#080")

    def _on_captured(self, info):
        def add():
            iid = self.ctree.insert("", 0, values=(info["time"], info["name"],
                                                   info.get("tab_title") or info["url"], info.get("job") or ""))
            self.captured[iid] = info
            self.ctree.selection_set(iid)
        self.root.after(0, add)

    def _cap_selected(self):
        sel = self.ctree.selection()
        return self.captured.get(sel[0]) if sel else None

    def _cap_open(self):
        info = self._cap_selected()
        if info:
            self.open_path(info["path"])

    def _cap_print(self):
        info = self._cap_selected()
        if not info:
            return
        cfg = dict(self._collect_print_opts())
        self.bg(lambda: capture.print_original(info["path"], cfg, title=info["name"]),
                lambda job: self.log("🖨 Đã in '%s' → %s" % (info["name"], job)))

    # ============================================== in theo điều kiện
    def _build_workflow(self, nb):
        outer = ttk.Frame(nb)
        nb.add(outer, text="🏷 In theo điều kiện")
        # khung cuộn được vì thẻ này nhiều mục
        canvas = tk.Canvas(outer, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        f = ttk.Frame(canvas, padding=10)
        f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=f, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for w in (canvas, f):
            w.bind("<Button-4>", lambda e: canvas.yview_scroll(-2, "units"))
            w.bind("<Button-5>", lambda e: canvas.yview_scroll(2, "units"))
        f.columnconfigure(1, weight=1)
        f.columnconfigure(3, weight=1)

        wf = dict(workflow.DEFAULT_WORKFLOW, **self.cfg.get("workflow", {}))
        self.wf_vars = {}

        def entry(row, col, key, label, width=28, pick=None, value=None):
            ttk.Label(f, text=label).grid(row=row, column=col, sticky="w", pady=2, padx=(0, 4))
            v = tk.StringVar(value=value if value is not None else str(wf.get(key, "")))
            ttk.Entry(f, textvariable=v, width=width).grid(row=row, column=col + 1, sticky="we", pady=2)
            if pick:
                ttk.Button(f, text="🎯", width=3, command=lambda: self.pick_element(v, pick)).grid(
                    row=row, column=col + 2, sticky="w")
            self.wf_vars[key] = v

        entry(0, 0, "name", "Tên quy trình:")
        entry(1, 0, "list_url", "URL danh sách (tùy chọn):")
        ttk.Button(f, text="Lấy từ tab", command=self._wf_url_from_tab).grid(row=1, column=2, sticky="w")
        entry(2, 0, "row_selector", "Dòng danh sách (CSS):", pick="row")
        tb = ttk.Frame(f)
        tb.grid(row=2, column=3, sticky="w", padx=6)
        ttk.Button(tb, text="🔎 Tự nhận diện", command=self.wf_inspect).pack(side="left")
        self.var_preset = tk.StringVar(value="Mẫu có sẵn...")
        cb = ttk.Combobox(tb, textvariable=self.var_preset, values=list(workflow.PRESETS), state="readonly", width=24)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.wf_apply_preset(self.var_preset.get()))
        entry(3, 0, "status_column", "Cột điều kiện:")
        entry(3, 2, "status_values", "  Chỉ in khi là:", value="; ".join(wf["status_values"]))
        entry(4, 0, "key_column", "Cột mã chứng từ (nên có):")
        entry(4, 2, "next_page_selector", "  Nút trang sau:", pick="el")
        entry(5, 0, "tag", "Thẻ đánh dấu đã in:")
        entry(5, 2, "tag_column", "  Cột thẻ:")

        o = ttk.Frame(f)
        o.grid(row=6, column=0, columnspan=5, sticky="w", pady=4)
        self.wf_bools = {}
        for key, text in (("wait_job", "Chờ máy in in xong mới gắn thẻ"),
                          ("verify_tag", "Kiểm tra thẻ đã hiện"),
                          ("close_new_tabs", "Đóng tab PDF sau khi in")):
            v = tk.BooleanVar(value=bool(wf.get(key)))
            ttk.Checkbutton(o, text=text, variable=v).pack(side="left", padx=(0, 10))
            self.wf_bools[key] = v
        o2 = ttk.Frame(f)
        o2.grid(row=7, column=0, columnspan=5, sticky="w")
        self.wf_nums = {}
        for key, text, hi in (("max_items", "Tối đa mỗi lượt:", 9999), ("every_minutes", "Tự chạy lại mỗi (phút, 0=tắt):", 1440),
                              ("pdf_timeout", "Chờ PDF (giây):", 600)):
            ttk.Label(o2, text=text).pack(side="left")
            v = tk.StringVar(value=str(wf.get(key)))
            ttk.Spinbox(o2, from_=0, to=hi, textvariable=v, width=6).pack(side="left", padx=(2, 12))
            self.wf_nums[key] = v

        self.wf_text = {}
        for col, key, title in ((0, "print_steps", "Bước bấm In cho 1 dòng"), (2, "tag_steps", "Bước gắn thẻ cho 1 dòng")):
            lf = ttk.LabelFrame(f, text=title, padding=4)
            lf.grid(row=8, column=col, columnspan=2, sticky="nsew", pady=6, padx=(0, 6) if col == 0 else 0)
            bar = ttk.Frame(lf)
            bar.pack(fill="x")
            btn = ttk.Button(bar, text="📹 Ghi thao tác")
            btn.configure(command=lambda k=key, b=btn: self._wf_record(k, b))
            btn.pack(side="left")
            ttk.Button(bar, text="Mẫu", command=lambda k=key: self._wf_set_steps(k, workflow.DEFAULT_WORKFLOW[k])).pack(side="left", padx=4)
            ttk.Button(bar, text="Xóa", command=lambda k=key: self._wf_set_steps(k, [])).pack(side="left")
            t = tk.Text(lf, height=9, width=36, font=("monospace", 9), undo=True)
            t.pack(fill="both", expand=True, pady=(4, 0))
            self.wf_text[key] = t
            self._wf_set_steps(key, wf[key])

        b = ttk.Frame(f)
        b.grid(row=9, column=0, columnspan=5, sticky="w", pady=4)
        ttk.Button(b, text="🔍 Chạy thử (không in)", command=lambda: self.wf_run(dry=True)).pack(side="left")
        self.btn_wf = ttk.Button(b, text="▶ CHẠY", command=self.wf_run)
        self.btn_wf.pack(side="left", padx=6, ipadx=8)
        ttk.Button(b, text="■ Dừng", command=lambda: self.wf_stop.set()).pack(side="left")
        ttk.Button(b, text="💾 Lưu", command=self.wf_save).pack(side="left", padx=6)
        ttk.Button(b, text="Xóa sổ đã in", command=self.wf_clear_ledger).pack(side="left")
        self.lbl_wf = ttk.Label(b, text="")
        self.lbl_wf.pack(side="left", padx=10)

        cols = ("label", "status", "tags", "result")
        self.wtree = ttk.Treeview(f, columns=cols, show="headings", height=7)
        for c, t, w in zip(cols, ("Chứng từ", "Tình trạng", "Thẻ", "Kết quả"), (130, 110, 90, 300)):
            self.wtree.heading(c, text=t)
            self.wtree.column(c, width=w, stretch=c in ("result", "label"))
        self.wtree.grid(row=10, column=0, columnspan=5, sticky="nsew")
        self.wf_stop = threading.Event()
        self.wf_thread = None
        self.wf_recording = None

    def _wf_set_steps(self, key, steps):
        t = self.wf_text[key]
        t.delete("1.0", "end")
        t.insert("1.0", "[\n" + ",\n".join("  " + json.dumps(s, ensure_ascii=False) for s in steps) + "\n]"
                 if steps else "[]")

    def _wf_url_from_tab(self):
        try:
            self.wf_vars["list_url"].set(self.selected_tab().get("url", ""))
        except CDPError as e:
            self.log("✖ %s" % e)

    def wf_apply_preset(self, name):
        p = workflow.PRESETS.get(name)
        if not p:
            return
        for k, v in p.items():
            if k in self.wf_vars:
                self.wf_vars[k].set("; ".join(v) if isinstance(v, list) else str(v))
            elif k in self.wf_text:
                self._wf_set_steps(k, v)
        self.log("Đã nạp mẫu '%s'. Bước bấm In cần ghi lại bằng '📹 Ghi thao tác' rồi bấm 'Chạy thử'." % name)

    def wf_inspect(self):
        """Đọc trang danh sách đang mở, tự điền dòng/cột và ghi báo cáo vào nhật ký."""
        col = self.wf_vars["status_column"].get().strip() or workflow.DEFAULT_WORKFLOW["status_column"]

        def apply(info):
            s = info["suggest"]
            if s["row_selector"]:
                self.wf_vars["row_selector"].set(s["row_selector"])
            if s["key_column"] and not self.wf_vars["key_column"].get().strip():
                self.wf_vars["key_column"].set(s["key_column"])
            self.wf_vars["tag_column"].set(s["tag_column"])
            if not s["tag_column"]:
                self.wf_bools["verify_tag"].set(False)
            if not self.wf_vars["list_url"].get().strip():
                self.wf_vars["list_url"].set(info["url"])
            self.log("🔎 Kết quả nhận diện:\n" + workflow.inspect_report(info, col))
            if not info["column_found"]:
                messagebox.showwarning("Chưa thấy cột", "Không thấy cột '%s' trên trang.\nCác cột đọc được:\n%s"
                                       % (col, "\n".join(info["headers"]) or "(không có)"))
        self.with_tab(lambda t: self.root.after(0, lambda i=workflow.inspect(t, col): apply(i)))

    def wf_collect(self):
        wf = {k: v.get().strip() for k, v in self.wf_vars.items()}
        wf["status_values"] = [s.strip() for s in wf["status_values"].replace(",", ";").split(";") if s.strip()]
        wf.update({k: bool(v.get()) for k, v in self.wf_bools.items()})
        for k, v in self.wf_nums.items():
            try:
                wf[k] = float(v.get()) if k == "every_minutes" else int(float(v.get()))
            except ValueError:
                raise ValueError("'%s' phải là số" % k)
        for k, t in self.wf_text.items():
            try:
                wf[k] = json.loads(t.get("1.0", "end") or "[]")
            except ValueError as e:
                raise ValueError("Các bước (%s) không phải JSON hợp lệ: %s" % (k, e))
            if not isinstance(wf[k], list):
                raise ValueError("Các bước (%s) phải là danh sách [...]" % k)
        if not wf["row_selector"] or not wf["status_column"]:
            raise ValueError("Cần nhập 'Dòng danh sách' và 'Cột điều kiện'")
        if not wf["print_steps"]:
            raise ValueError("Chưa có bước bấm In")
        return wf

    def wf_save(self):
        try:
            self.cfg["workflow"] = self.wf_collect()
        except ValueError as e:
            return messagebox.showerror("Quy trình chưa hợp lệ", str(e))
        self.save_cfg()
        self.log("💾 Đã lưu quy trình '%s'" % self.cfg["workflow"]["name"])

    def wf_clear_ledger(self):
        name = self.wf_vars["name"].get().strip()
        led = workflow.Ledger(name)
        if messagebox.askyesno("Xóa sổ đã in", "Sổ của quy trình '%s' đang ghi %d chứng từ đã in.\n"
                               "Xóa thì các chứng từ chưa có thẻ có thể bị in lại. Tiếp tục?" % (name, led.count())):
            self.log("Đã xóa %d mục trong sổ đã in" % led.clear())

    def wf_run(self, dry=False):
        if self.wf_thread and self.wf_thread.is_alive():
            return self.log("Quy trình đang chạy")
        try:
            wf = self.wf_collect()
        except ValueError as e:
            return messagebox.showerror("Quy trình chưa hợp lệ", str(e))
        if not dry and not messagebox.askyesno(
                "Chạy quy trình", "Sẽ IN các chứng từ có '%s' là: %s\nvà gắn thẻ '%s' sau khi in.\n\n"
                "Nên bấm 'Chạy thử' trước để xem danh sách. Chạy ngay?"
                % (wf["status_column"], ", ".join(wf["status_values"]), wf["tag"])):
            return
        self.cfg["workflow"] = wf
        self.save_cfg()
        try:
            info = self.selected_tab()
        except CDPError as e:
            return self.log("✖ %s" % e)
        cfg = dict(self._collect_print_opts())
        self.wtree.delete(*self.wtree.get_children())
        self.wf_stop.clear()
        self.lbl_wf.configure(text="● Đang chạy thử..." if dry else "● Đang chạy...", foreground="#080")

        def on_row(r):
            self.root.after(0, lambda: self.wtree.insert("", "end", values=(
                r.get("label"), r.get("status") or "", (r.get("tags") or "")[:40] if r.get("tags") != r.get("text") else "",
                r.get("result"))))

        def job():
            try:
                br = self.browser()
                with br.connect(info) as tab:
                    workflow.WorkflowRunner(cfg, wf, self.log, on_row, self.wf_stop).run(tab, dry_run=dry)
            except (CDPError, printing.PrintError, OSError) as e:
                self.log("✖ Quy trình dừng: %s" % e)
            finally:
                self.root.after(0, lambda: self.lbl_wf.configure(text=""))
        self.wf_thread = threading.Thread(target=job, daemon=True)
        self.wf_thread.start()

    def _wf_record(self, key, btn):
        if self.wf_recording:
            self.wf_recording.set()
            return
        try:
            info = self.selected_tab()
        except CDPError as e:
            return self.log("✖ %s" % e)
        row_sel = self.wf_vars["row_selector"].get().strip()
        stop = threading.Event()
        self.wf_recording = stop
        btn.configure(text="⏹ Dừng ghi")
        self._wf_set_steps(key, [])
        steps = []
        self.log("📹 Đang ghi: hãy thao tác trên trình duyệt (với MỘT dòng chứng từ), xong bấm 'Dừng ghi'")

        def job():
            br = self.browser()
            try:
                br.activate(info["id"])
                with br.connect(info) as tab:
                    while not stop.is_set():
                        try:
                            got = tab.evaluate("window.__bpRecFlush ? window.__bpRecFlush() : null")
                            if got is None:      # trang mới tải lại -> gắn lại bộ ghi
                                tab.evaluate(RECORD_JS % json.dumps(row_sel))
                            elif got:
                                for st in got:
                                    if not steps or steps[-1] != st:
                                        steps.append(st)
                                self.root.after(0, lambda s=list(steps): self._wf_set_steps(key, s))
                        except CDPError:
                            pass
                        stop.wait(0.4)
                    got = tab.evaluate("(()=>{const r=window.__bpRecFlush?window.__bpRecFlush():[];"
                                       "if(window.__bpRecStop)window.__bpRecStop();return r;})()")
                    for st in got or []:
                        if not steps or steps[-1] != st:
                            steps.append(st)
                    self.root.after(0, lambda s=list(steps): self._wf_set_steps(key, s))
            except CDPError as e:
                self.log("✖ %s" % e)
            finally:
                def done():
                    self.wf_recording = None
                    btn.configure(text="📹 Ghi thao tác")
                    self.log("📹 Đã ghi %d bước — kiểm tra lại, có thể sửa chữ trong ô" % len(steps))
                self.root.after(0, done)
        threading.Thread(target=job, daemon=True).start()

    # ============================================================ browser
    def launch_browser(self):
        path = self.var_browser.get().strip()
        self.cfg["browser_path"] = path
        br = self.browser()
        self.log("Đang mở trình duyệt %s ..." % (path or "(tự tìm)"))
        self.bg(lambda: launcher.launch(path or None, br.port, self.cfg.get("profile_dir"), host=br.host),
                lambda _: (self.log("✔ Trình duyệt đã sẵn sàng"), self.save_cfg(), self.refresh_tabs()))

    def refresh_tabs(self, quiet=False):
        if self._refreshing:
            return
        self._refreshing = True
        br = self.browser()

        def done(tabs):
            self._refreshing = False
            self._fill_tabs(tabs)

        def job():
            try:
                return br.tabs()
            except CDPError as e:
                if not quiet:
                    self.log("✖ %s" % e)
                return None
            finally:
                self._refreshing = False
        self.bg(job, done)

    def _fill_tabs(self, tabs):
        if tabs is None:
            self.tabs = []
            self.tree.delete(*self.tree.get_children())
            self.lbl_status.configure(text="● Chưa kết nối", foreground="#b00")
            return
        prev = self.tree.selection()
        self.tabs = tabs
        self.tree.delete(*self.tree.get_children())
        for t in tabs:
            self.tree.insert("", "end", iid=t["id"], values=(t.get("title", ""), t.get("url", "")))
        if prev and self.tree.exists(prev[0]):
            self.tree.selection_set(prev[0])
        elif tabs:
            self.tree.selection_set(tabs[0]["id"])
        self.lbl_status.configure(text="● Đã kết nối — %d tab" % len(tabs), foreground="#080")

    def _auto_refresh(self):
        self.refresh_tabs(quiet=True)
        self.root.after(3000, self._auto_refresh)

    def activate_tab(self):
        try:
            t = self.selected_tab()
        except CDPError as e:
            return self.log("✖ %s" % e)
        br = self.browser()
        self.bg(lambda: br.activate(t["id"]))

    def new_tab(self):
        url = self.ask("Tab mới", "Địa chỉ (URL):", "https://")
        if url:
            br = self.browser()
            self.bg(lambda: br.new_tab(url), lambda _: self.refresh_tabs())

    def close_tab(self):
        try:
            t = self.selected_tab()
        except CDPError as e:
            return self.log("✖ %s" % e)
        br = self.browser()
        self.bg(lambda: br.close_tab(t["id"]), lambda _: self.refresh_tabs())

    def pick_element(self, var, mode="el"):
        self.log("🎯 Hãy chuyển sang trình duyệt và click vào %s..." %
                 ("một dòng chứng từ" if mode == "row" else "vùng cần chọn"))

        def action(tab):
            tab.evaluate("window.__bpPickMode=%s" % json.dumps(mode))
            tab.evaluate(PICKER_JS)
            for _ in range(240):
                time.sleep(0.5)
                val = tab.evaluate("window.__bpPicked")
                if val is not None:
                    if val:
                        self.root.after(0, lambda: var.set(val))
                        self.log("✔ Đã chọn: %s" % val)
                    else:
                        self.log("Đã hủy chọn")
                    return
            self.log("Hết thời gian chọn phần tử")
        self.with_tab(action, activate=True)

    # ============================================================ printing
    def refresh_printers(self):
        def job():
            if not printing.cups_available():
                self.log("⚠ Chưa có CUPS (lệnh lp). Cài: sudo apt install cups cups-client")
                return [], None
            return printing.list_printers(), printing.default_printer()

        def done(res):
            names, default = res
            self.cb_printer["values"] = names
            if not self.var_printer.get() and default:
                self.var_printer.set(default)
            self.printers = names
            if names:
                self.log("Máy in: %s (mặc định: %s)" % (", ".join(names), default or "chưa đặt"))
        self.bg(job, done)

    def _collect_print_opts(self):
        try:
            self.cfg.update(printer=self.var_printer.get().strip(), copies=int(self.var_copies.get()),
                            paper=self.var_paper.get(), landscape=bool(self.var_land.get()),
                            margin_mm=int(self.var_margin.get()), keep_pdf=bool(self.var_keep.get()),
                            fit_to_page=bool(self.var_fit.get()))
        except (tk.TclError, ValueError):
            pass
        return self.cfg

    def _print(self, **kw):
        c = self._collect_print_opts()
        sel = self.var_print_sel.get().strip()

        def action(tab):
            pdf, job = print_tab(tab, printer=c["printer"], copies=c["copies"], paper=c["paper"],
                                 landscape=c["landscape"], selector=sel, margin_mm=c["margin_mm"],
                                 keep_pdf=kw.get("keep", c["keep_pdf"]), pdf_path=kw.get("path"),
                                 send_to_printer=kw.get("send", True),
                                 fit_to_page=c["fit_to_page"], log=self.log)
            if job:
                self.log("🖨 Đã gửi lệnh in %s (%d bản) — %s" % (job, c["copies"], pdf))
            else:
                self.log("💾 Đã lưu %s" % pdf)
            if kw.get("open"):
                self.open_path(pdf)
        self.with_tab(action)

    def do_print(self):
        self.save_cfg()
        self._print()

    def do_preview(self):
        self._print(send=False, keep=True, open=True)

    def do_save_pdf(self):
        path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if path:
            self._print(send=False, path=path)

    def do_screenshot(self):
        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", "*.png")])
        if path:
            self.with_tab(lambda t: self.log("📷 Đã lưu %s" % t.screenshot(path, full_page=True)))

    def show_jobs(self):
        self.bg(printing.jobs, lambda j: self.log("Hàng đợi in: " + ("; ".join(j) if j else "trống")))

    # =============================================================== macro
    def macro_example(self):
        self.txt_macro.delete("1.0", "end")
        self.txt_macro.insert("1.0", json.dumps(macro.EXAMPLE, ensure_ascii=False, indent=2))

    def macro_open(self):
        path = filedialog.askopenfilename(initialdir=config.MACRO_DIR, filetypes=[("Macro JSON", "*.json")])
        if path:
            try:
                data = macro.load(path)
            except (OSError, ValueError) as e:
                return messagebox.showerror("Lỗi", str(e))
            self.txt_macro.delete("1.0", "end")
            self.txt_macro.insert("1.0", json.dumps(data, ensure_ascii=False, indent=2))

    def macro_save(self):
        try:
            data = json.loads(self.txt_macro.get("1.0", "end"))
        except ValueError as e:
            return messagebox.showerror("JSON không hợp lệ", str(e))
        path = filedialog.asksaveasfilename(initialdir=config.MACRO_DIR, defaultextension=".json",
                                            filetypes=[("Macro JSON", "*.json")])
        if path:
            macro.save(path, data)
            self.log("💾 Đã lưu macro %s" % path)

    def macro_run(self):
        try:
            steps = json.loads(self.txt_macro.get("1.0", "end"))
        except ValueError as e:
            return messagebox.showerror("JSON không hợp lệ", str(e))
        variables = {}
        for part in self.var_macro_vars.get().split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                variables[k.strip()] = v.strip()
        self.macro_stop.clear()
        cfg = self._collect_print_opts()
        self.log("▶ Chạy macro")
        self.with_tab(lambda t: macro.run(t, steps, cfg, variables, self.log, self.macro_stop))

    def macro_help(self):
        lines = ["Mỗi bước là một đối tượng JSON có trường \"action\":", ""]
        lines += ["• %-10s %s" % (k, v) for k, v in macro.ACTIONS.items()]
        lines += ["", "Dùng {{ten_bien}} để chèn biến; {{title}} và {{url}} có sẵn."]
        messagebox.showinfo("Hướng dẫn macro", "\n".join(lines))

    # =============================================================== rules
    def refresh_rules(self):
        self.rtree.delete(*self.rtree.get_children())
        for i, r in enumerate(self.cfg["rules"]):
            self.rtree.insert("", "end", iid=str(i), values=(
                "✔" if r.get("enabled") else "", r.get("name"),
                r.get("url_pattern") or ("tiêu đề: " + r.get("title_pattern", "")),
                r.get("selector") or "(cả trang)", r.get("printer") or "(mặc định)", r.get("copies", 1)))

    def _rule_index(self):
        sel = self.rtree.selection()
        return int(sel[0]) if sel else None

    def rule_add(self, preset=None):
        rule = RuleDialog(self, dict(config.DEFAULT_RULE, **(preset or {}))).result
        if rule:
            self.cfg["rules"].append(rule)
            self.save_cfg()
            self.refresh_rules()

    def rule_from_tab(self):
        try:
            t = self.selected_tab()
        except CDPError as e:
            return self.log("✖ %s" % e)
        import re
        from urllib.parse import urlsplit
        u = urlsplit(t.get("url", ""))
        # thay các đoạn số (mã chứng từ) bằng \d+ để khớp mọi chứng từ cùng loại
        path = re.sub(r"\d+", r"\\d+", re.escape(u.path))
        self.rule_add({"name": t.get("title", "")[:40], "url_pattern": re.escape(u.netloc) + path,
                       "selector": self.var_print_sel.get().strip(), "printer": self.var_printer.get(),
                       "paper": self.var_paper.get()})

    def rule_edit(self):
        i = self._rule_index()
        if i is None:
            return
        rule = RuleDialog(self, dict(self.cfg["rules"][i])).result
        if rule:
            self.cfg["rules"][i] = rule
            self.save_cfg()
            self.refresh_rules()

    def rule_toggle(self):
        i = self._rule_index()
        if i is not None:
            self.cfg["rules"][i]["enabled"] = not self.cfg["rules"][i].get("enabled")
            self.save_cfg()
            self.refresh_rules()

    def rule_delete(self):
        i = self._rule_index()
        if i is not None and messagebox.askyesno("Xóa", "Xóa quy tắc '%s'?" % self.cfg["rules"][i]["name"]):
            del self.cfg["rules"][i]
            self.save_cfg()
            self.refresh_rules()

    def toggle_watch(self):
        if self.watcher.running:
            self.watcher.stop()
            self.btn_watch.configure(text="▶ BẮT ĐẦU THEO DÕI")
            self.lbl_watch.configure(text="Đang tắt", foreground="#b00")
        else:
            self.browser()
            self._collect_print_opts()
            self.watcher.start()
            self.btn_watch.configure(text="■ DỪNG THEO DÕI")
            self.lbl_watch.configure(text="● Đang theo dõi", foreground="#080")

    def _watch_start_changed(self):
        self.cfg["watch_on_start"] = bool(self.var_watch_start.get())
        self.save_cfg()

    # ================================================================ misc
    def ask(self, title, prompt, initial=""):
        from tkinter import simpledialog
        return simpledialog.askstring(title, prompt, initialvalue=initial, parent=self.root)

    def open_path(self, path):
        if not os.path.splitext(path)[1]:
            os.makedirs(path, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            self.log("Mở thủ công: %s" % path)

    def on_close(self):
        self.save_cfg()
        self.watcher.stop()
        self.wf_stop.set()
        if self.capture.running:
            self.capture.stop()
        self.root.destroy()


class RuleDialog(tk.Toplevel):
    FIELDS = [
        ("name", "Tên quy tắc", "str"),
        ("url_pattern", "URL khớp (regex)", "str"),
        ("title_pattern", "Tiêu đề khớp (regex, tùy chọn)", "str"),
        ("selector", "Vùng in (CSS selector)", "sel"),
        ("wait_selector", "Chờ phần tử (tùy chọn)", "sel"),
        ("delay", "Chờ thêm (giây)", "float"),
        ("printer", "Máy in", "printer"),
        ("copies", "Số bản", "int"),
        ("paper", "Khổ giấy", "paper"),
        ("landscape", "In ngang", "bool"),
        ("close_tab", "Đóng tab sau khi in", "bool"),
        ("enabled", "Bật", "bool"),
    ]

    def __init__(self, app, rule):
        super().__init__(app.root)
        self.app = app
        self.result = None
        self.title("Quy tắc tự động in")
        self.transient(app.root)
        self.resizable(True, False)
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        self.vars = {}
        for row, (key, label, kind) in enumerate(self.FIELDS):
            ttk.Label(f, text=label + ":").grid(row=row, column=0, sticky="w", pady=2)
            if kind == "bool":
                v = tk.BooleanVar(value=bool(rule.get(key)))
                ttk.Checkbutton(f, variable=v).grid(row=row, column=1, sticky="w")
            else:
                v = tk.StringVar(value=str(rule.get(key, "")))
                if kind == "printer":
                    w = ttk.Combobox(f, textvariable=v, values=[""] + list(getattr(app, "printers", [])))
                elif kind == "paper":
                    w = ttk.Combobox(f, textvariable=v, values=list(PAPER_SIZES), state="readonly")
                else:
                    w = ttk.Entry(f, textvariable=v, width=44)
                w.grid(row=row, column=1, sticky="we", pady=2)
                if kind == "sel":
                    ttk.Button(f, text="🎯", width=3, command=lambda v=v: app.pick_element(v)).grid(row=row, column=2)
            self.vars[key] = (v, kind)
        ttk.Label(f, text="Ví dụ URL: erp\\.congty\\.vn/phieu-thu/\\d+   hoặc   /invoice/print",
                  foreground="#666").grid(row=len(self.FIELDS), column=0, columnspan=3, sticky="w", pady=6)
        b = ttk.Frame(f)
        b.grid(row=len(self.FIELDS) + 1, column=0, columnspan=3, sticky="e")
        ttk.Button(b, text="Lưu", command=self.ok).pack(side="left", padx=4)
        ttk.Button(b, text="Hủy", command=self.destroy).pack(side="left")
        self.grab_set()
        self.wait_window(self)

    def ok(self):
        import re
        out = {}
        try:
            for key, (v, kind) in self.vars.items():
                val = v.get()
                if kind == "bool":
                    out[key] = bool(val)
                elif kind == "int":
                    out[key] = int(val)
                elif kind == "float":
                    out[key] = float(val)
                else:
                    out[key] = val.strip()
            for k in ("url_pattern", "title_pattern"):
                if out[k]:
                    re.compile(out[k])
        except ValueError as e:
            return messagebox.showerror("Giá trị không hợp lệ", str(e), parent=self)
        except re.error as e:
            return messagebox.showerror("Regex không hợp lệ", str(e), parent=self)
        if not out["url_pattern"] and not out["title_pattern"]:
            return messagebox.showerror("Thiếu", "Cần nhập URL khớp hoặc tiêu đề khớp", parent=self)
        self.result = out
        self.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
