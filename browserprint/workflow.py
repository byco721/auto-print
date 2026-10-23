"""In theo điều kiện.

Quét danh sách chứng từ đang hiển thị trên web (MISA AMIS, phần mềm bán hàng...),
chỉ in những dòng thỏa điều kiện — ví dụ cột "Tình trạng ghi doanh số" là
"Đề nghị ghi" hoặc "Đã ghi" và chưa có thẻ "Đã in" — rồi thao tác gắn thẻ "Đã in"
lên chứng từ sau khi máy in đã in xong.

Cột được tìm theo TÊN tiêu đề cột và vị trí ngang trên màn hình, nên dùng được cả
bảng HTML thường lẫn các lưới dữ liệu tách phần tiêu đề/thân (DevExtreme, AG Grid...).

Sổ ghi nhớ ~/.local/share/browser-print/printed.json lưu các chứng từ đã in, để
không bao giờ in trùng (kể cả khi bước gắn thẻ bị lỗi — lần sau chỉ gắn thẻ lại).
"""

import copy
import json
import os
import queue
import threading
import time
import unicodedata

from . import capture, macro, printing
from .cdp import Browser, CDPError
from .config import DATA_DIR
from .jobs import print_tab

LEDGER_FILE = os.path.join(DATA_DIR, "printed.json")
ROW_MARK = '[data-bp-row="1"]'

DEFAULT_WORKFLOW = {
    "name": "In chứng từ đã ghi doanh số",
    "list_url": "",                           # trống = dùng trang đang mở ở tab
    "row_selector": "table tbody tr",         # CSS của MỘT dòng dữ liệu (áp cho mọi dòng)
    "key_column": "",                         # cột mã chứng từ, vd "Số đơn hàng" (nên có)
    "status_column": "Tình trạng ghi DS|Tình trạng ghi doanh số",   # "|" = nhiều cách viết
    "status_values": ["Đề nghị ghi", "Đã ghi"],
    "tag": "Đã in",
    "tag_column": "Thẻ",                      # trống = tìm thẻ trong cả dòng
    "print_steps": [
        {"action": "click_text", "text": "In", "scope": "row"},
    ],
    "tag_steps": [
        {"action": "click", "selector": "input[type=checkbox]", "scope": "row"},
        {"action": "click_text", "text": "Gắn thẻ"},
        {"action": "click_text", "text": "{{tag}}", "exact": True},
        {"action": "click_text", "text": "Đồng ý"},
    ],
    "pdf_timeout": 60,        # chờ máy chủ trả PDF sau khi bấm In (giây)
    "wait_job": True,         # chờ máy in in xong rồi mới gắn thẻ
    "job_timeout": 180,
    "verify_tag": True,       # kiểm tra lại thẻ đã hiện trên dòng
    "close_new_tabs": True,   # đóng tab xem PDF mở ra khi in
    "next_page_selector": "", # nút "Trang sau" (trống = chỉ trang hiện tại)
    "max_items": 100,         # tối đa số chứng từ mỗi lượt
    "every_minutes": 0,       # >0: tự chạy lại sau mỗi N phút
    "printer": "",            # trống = máy in ở thẻ In chứng từ
    "copies": 0,              # 0 = số bản ở thẻ In chứng từ
}

# Tìm ô tiêu đề cột theo tên. Chỉ nhận chữ nằm PHÍA TRÊN dòng dữ liệu đầu tiên (bỏ qua
# bảng "Tùy chỉnh cột", bộ lọc... cũng chứa tên cột) và ưu tiên ô tiêu đề thật (th).
FIND_HEADER_JS = r"""
  const findHeader = (cand, name, top, inRow) => {
    const names = name.split('|').map(low).filter(Boolean);   // nhiều cách viết: "A|B"
    let best = null, bestScore = Infinity;
    for (const el of cand) {
      const tc = low(el.textContent);
      if (!names.some(n => tc.includes(n)) || inRow(el)) continue;
      const t = low(el.innerText);
      if (!names.some(n => t === n || (t.startsWith(n) && t.length <= n.length + 3))) continue;
      if (!vis(el)) continue;
      const cell = el.closest('th,[role=columnheader],[class*=header-cell]') || el;
      const r = cell.getBoundingClientRect();
      if (r.top > top + 2) continue;
      const score = (cell.matches('th,[role=columnheader],[class*=header-cell]') ? 0 : 1e9) + r.width * r.height;
      if (score < bestScore) { best = cell; bestScore = score; }
    }
    return best;
  };
"""

# Mẫu cấu hình cho các phần mềm đã khảo sát. Các bước được dựng từ cấu trúc trang thật;
# bước bấm In phụ thuộc mẫu in mỗi công ty chọn nên cần ghi lại bằng "📹 Ghi thao tác".
PRESETS = {
    "MISA AMIS CRM — Đơn hàng": {
        "name": "MISA - In đơn hàng đã ghi doanh số",
        "row_selector": "table.jsgrid-table.JColResizer > tbody > tr",
        "key_column": "Số đơn hàng",
        "status_column": "Tình trạng ghi DS|Tình trạng ghi doanh số",
        "status_values": ["Đề nghị ghi", "Đã ghi"],
        "tag": "Đã in",
        "tag_column": "Thẻ",
        "next_page_selector": "",   # mặc định MISA hiện 100 dòng/trang
        # Khảo sát trên MISA thật (người dùng xác nhận 23/10/2026): tích đơn -> nút "Trộn mẫu" trên
        # thanh công cụ -> hộp "Chọn mẫu" -> click tên mẫu -> hộp "In Đơn hàng"
        # nạp PDF do máy chủ tạo (iframe .../doctemplate/download/file?token=..., application/pdf).
        # App lấy thẳng file PDF đó, KHÔNG bấm nút "In" của MISA (nút này mở hộp thoại in của trình duyệt).
        "print_steps": [
            # Bỏ chọn các dòng người dùng đang tích (nếu có), rồi chỉ tích đúng dòng cần in
            {"action": "click_text", "text": "Bỏ chọn", "exact": True, "optional": True, "timeout": 2},
            {"action": "click", "selector": "input.grid-checkbox", "scope": "row"},
            {"action": "click_text", "text": "Trộn mẫu", "exact": True},
            {"action": "click_text", "text": "IN A5_032026", "exact": True},   # tên mẫu in của công ty
            {"action": "wait_pdf"},
            # đóng hộp xem trước (nằm trên) rồi hộp Chọn mẫu
            {"action": "click", "selector": ".custom-dialog-fullScreen .icon-close-dialog", "optional": True, "timeout": 3},
            {"action": "wait", "seconds": 0.5},
            {"action": "click", "selector": ".cdk-overlay-pane.misa-dialog .icon-close-dialog", "optional": True, "timeout": 3},
        ],
        "tag_steps": [
            # QUAN TRỌNG: bỏ chọn trước, nếu không thẻ sẽ gắn cho cả các đơn đang được tích sẵn
            {"action": "click_text", "text": "Bỏ chọn", "exact": True, "optional": True, "timeout": 2},
            {"action": "click", "selector": "input.grid-checkbox", "scope": "row"},
            {"action": "click_text", "text": "Gắn thẻ", "exact": True},
            {"action": "fill", "selector": "input[placeholder='Nhập thẻ']", "value": "{{tag}}"},
            # KHÔNG tích "Thay thế các thẻ đã gắn..." — sẽ xóa các thẻ cũ của đơn
            {"action": "click_text", "text": "Lưu", "exact": True},
        ],
    },
}

SCAN_JS = r"""
(()=>{
  const ROW = %s, COLS = %s;
  const norm = s => (s || '').normalize('NFC').replace(/\s+/g, ' ').trim();
  const low = s => norm(s).toLowerCase();
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
""" + FIND_HEADER_JS.replace("%", "%%") + r"""
  let rows;
  // dòng có ô tiêu đề (th) là hàng tiêu đề, không phải dòng dữ liệu
  try { rows = [...document.querySelectorAll(ROW)].filter(r => vis(r) && !r.querySelector('th,[role=columnheader]')); }
  catch (e) { return {error: 'Dòng danh sách không hợp lệ: ' + ROW}; }
  const rowSet = new Set(rows);
  const heads = {}, missing = [];
  const cand = [...document.querySelectorAll('th,td,div,span,a,label,p,[role=columnheader]')];
  const top = rows.length ? rows[0].getBoundingClientRect().top : Infinity;
  for (const name of COLS) {
    const best = findHeader(cand, name, top, el => rowSet.has(el.closest(ROW)));
    if (best) { const r = best.getBoundingClientRect(); heads[name] = (r.left + r.right) / 2; }
    else missing.push(name);
  }
  const out = rows.map((row, i) => {
    let cells = [...row.querySelectorAll('td,[role=gridcell],[role=cell]')];
    if (!cells.length) cells = [...row.children];
    const data = {};
    for (const name in heads) {
      const x = heads[name];
      const c = cells.filter(c => { const r = c.getBoundingClientRect(); return r.width > 0 && r.left <= x && x <= r.right; }).pop();
      data[name] = c ? norm(c.innerText) : null;
    }
    return {i, text: norm(row.innerText).slice(0, 400), cells: data};
  });
  return {rows: out, missing};
})()
"""

MARK_JS = r"""
(()=>{
  document.querySelectorAll('[data-bp-row]').forEach(e => e.removeAttribute('data-bp-row'));
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const row = [...document.querySelectorAll(%s)].filter(r => vis(r) && !r.querySelector('th,[role=columnheader]'))[%d];
  if (!row) return false;
  row.setAttribute('data-bp-row', '1');
  row.scrollIntoView({block: 'center'});
  return true;
})()
"""

NEXT_JS = r"""
(()=>{
  const e = document.querySelector(%s);
  if (!e) return false;
  const r = e.getBoundingClientRect();
  if (!(r.width > 0 && r.height > 0)) return false;
  if (e.disabled || e.getAttribute('aria-disabled') === 'true' || /disabled/i.test(e.className || '')) return false;
  return true;
})()
"""


INSPECT_JS = r"""
(()=>{
  const COL = %s;
  const norm = s => (s || '').normalize('NFC').replace(/\s+/g, ' ').trim();
  const low = s => norm(s).toLowerCase();
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
""" + FIND_HEADER_JS.replace("%", "%%") + r"""
  const cssPath = (el) => { const parts = [];
    while (el && el.nodeType === 1 && el !== document.documentElement) {
      // id có chữ số thường do thư viện tự sinh (JColResizer3...) -> không dùng
      if (el.id && !/\d/.test(el.id) && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) { parts.unshift('#' + CSS.escape(el.id)); break; }
      let s = el.tagName.toLowerCase();
      const cls = [...el.classList].filter(c => !/(hover|active|focus|select|state|alt|odd|even|^ng-|^cdk-)/i.test(c)).slice(0, 2);
      if (cls.length) s += '.' + cls.map(c => CSS.escape(c)).join('.');
      const p = el.parentElement;
      if (p) { const sib = [...p.children].filter(c => c.tagName === el.tagName); if (sib.length > 1) s += ':nth-of-type(' + (sib.indexOf(el) + 1) + ')'; }
      parts.unshift(s); el = p;
      // đủ duy nhất VÀ có tên class -> dừng cho ngắn gọn ("tbody" trơn quá chung, hộp thoại có bảng sẽ khớp nhầm)
      const path = parts.join(' > ');
      if (parts.length >= 2 && /[.#]/.test(path) && document.querySelectorAll(path).length === 1) break;
    }
    return parts.join(' > '); };
  // 1. nhóm dòng lặp lại nhiều nhất = danh sách chứng từ
  const groups = new Map();
  for (const r of document.querySelectorAll('tr,[role=row]')) {
    if (!vis(r) || !r.parentElement || r.querySelector('th,[role=columnheader]')) continue;
    if ((r.querySelectorAll('td,[role=gridcell],[role=cell]').length || r.children.length) < 3) continue;
    const g = groups.get(r.parentElement) || []; g.push(r); groups.set(r.parentElement, g);
  }
  let rows = [], parent = null;
  for (const [p, g] of groups) if (g.length > rows.length) { rows = g; parent = p; }
  let rowSel = '';
  if (rows.length) {
    const r0 = rows[0];
    const common = [...r0.classList].filter(c => !/(hover|active|focus|select|alt|odd|even)/i.test(c) &&
                    rows.filter(s => s.classList.contains(c)).length >= rows.length * 0.9).slice(0, 2);
    rowSel = cssPath(parent) + ' > ' + r0.tagName.toLowerCase() + common.map(c => '.' + CSS.escape(c)).join('') +
             (r0.getAttribute('role') === 'row' ? '[role=row]' : '');
  }
  // 2. tiêu đề cột: hàng tiêu đề chứa cột điều kiện, nếu không thì mọi th/columnheader
  const top = rows.length ? rows[0].getBoundingClientRect().top : Infinity;
  const headerEl = findHeader([...document.querySelectorAll('th,td,div,span,[role=columnheader]')], COL, top,
                              el => rows.some(r => r.contains(el)));
  let headers;
  const hrow = (headerEl && headerEl.closest('tr,[role=row]')) ||
      [...document.querySelectorAll('thead tr,tr[class*=header],[role=row][class*=header],[class*=header-row]')]
        .filter(r => vis(r) && !rows.includes(r) && r.querySelector('th,td,[role=columnheader]'))[0];
  if (hrow) headers = [...hrow.querySelectorAll('th,td,[role=columnheader]')].map(c => norm(c.innerText));
  else headers = [...document.querySelectorAll('th,[role=columnheader]')].filter(vis).map(c => norm(c.innerText));
  headers = headers.filter(h => h && h.length < 60);
  // 3. giá trị cột điều kiện
  const values = {};
  if (headerEl) {
    const b = headerEl.getBoundingClientRect(), x = (b.left + b.right) / 2;
    for (const r of rows) {
      let cells = [...r.querySelectorAll('td,[role=gridcell],[role=cell]')]; if (!cells.length) cells = [...r.children];
      const c = cells.filter(c => { const q = c.getBoundingClientRect(); return q.left <= x && x <= q.right; }).pop();
      const v = c ? norm(c.innerText) : '(?)'; values[v] = (values[v] || 0) + 1;
    }
  }
  const btnText = root => [...new Set([...root.querySelectorAll('button,a,[role=button],[role=menuitem],[title]')]
      .filter(vis).map(b => norm(b.innerText || b.title || b.getAttribute('aria-label'))).filter(t => t && t.length <= 30))];
  const toolbar = btnText(document).filter(t => !rows.some(r => r.innerText.includes(t)) &&
      /(in|thẻ|tag|xuất|pdf|gắn|nhãn|trang|›|»|next)/i.test(t));
  return {row_selector: rowSel, row_count: rows.length, column_found: !!headerEl, headers, values,
          row_buttons: rows.length ? btnText(rows[0]) : [], row_icons: rows.length ? rows[0].querySelectorAll('button,[role=button],i,svg').length : 0,
          toolbar, url: location.href, title: document.title};
})()
"""


def inspect(tab, status_column=DEFAULT_WORKFLOW["status_column"]):
    """Tự nhận diện danh sách chứng từ trên trang và gợi ý cấu hình."""
    info = tab.evaluate(INSPECT_JS % json.dumps(status_column))
    hs = info["headers"]
    def pick(*words):   # ưu tiên theo thứ tự từ khóa
        return next((h for w in words for h in hs if norm(h).startswith(w)), "")
    info["suggest"] = {
        "row_selector": info["row_selector"],
        "key_column": pick("số đơn", "số chứng từ", "số ct", "mã đơn", "số hóa đơn", "số phiếu", "mã"),
        "tag_column": pick("thẻ", "nhãn", "tag"),
    }
    return info


def inspect_report(info, status_column):
    lines = ["Trang: %s" % info["title"],
             "Dòng danh sách: %s (%d dòng)" % (info["row_selector"] or "KHÔNG TÌM THẤY", info["row_count"]),
             "Các cột: %s" % " | ".join(info["headers"]) if info["headers"] else "Các cột: (không đọc được tiêu đề)",
             "Cột '%s': %s" % (status_column, "có" if info["column_found"] else "KHÔNG THẤY — kiểm tra lại tên cột"),
             ]
    if info["values"]:
        lines.append("Giá trị trong cột: %s" % ", ".join("%s (%d)" % kv for kv in info["values"].items()))
    lines.append("Nút/chữ trong dòng đầu: %s (có %d nút/biểu tượng)" % (
        ", ".join(info["row_buttons"]) or "(không có chữ)", info["row_icons"]))
    lines.append("Nút liên quan ngoài danh sách: %s" % (", ".join(info["toolbar"]) or "(không thấy)"))
    s = info["suggest"]
    lines.append("Gợi ý: cột mã = '%s', cột thẻ = '%s'" % (s["key_column"] or "?", s["tag_column"] or "(không có)"))
    return "\n".join(lines)


def norm(s):
    return " ".join(unicodedata.normalize("NFC", s or "").split()).lower()


class Ledger:
    """Sổ ghi nhớ chứng từ đã in, theo tên quy trình."""
    _lock = threading.Lock()

    def __init__(self, name):
        self.name = name

    @staticmethod
    def _load():
        try:
            with open(LEDGER_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def get(self, key):
        return self._load().get(self.name, {}).get(key)

    def update(self, key, **fields):
        with self._lock:
            data = self._load()
            entry = data.setdefault(self.name, {}).setdefault(key, {})
            entry.update(fields)
            os.makedirs(os.path.dirname(LEDGER_FILE), exist_ok=True)
            tmp = LEDGER_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, LEDGER_FILE)

    def clear(self):
        with self._lock:
            data = self._load()
            n = len(data.pop(self.name, {}))
            with open(LEDGER_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            return n

    def count(self):
        return len(self._load().get(self.name, {}))


class WorkflowRunner:
    def __init__(self, cfg, wf, log=print, on_row=None, stop_event=None):
        self.cfg = cfg
        self.wf = dict(copy.deepcopy(DEFAULT_WORKFLOW), **(wf or {}))
        self.log = log
        self.on_row = on_row or (lambda row: None)
        self.stop = stop_event or threading.Event()
        self.ledger = Ledger(self.wf["name"])
        self.browser = Browser(cfg["host"], cfg["port"])
        self.expect = None

    # ----------------------------------------------------------- đọc bảng
    def _columns(self):
        cols = []
        for c in (self.wf["status_column"], self.wf["key_column"], self.wf["tag_column"]):
            if c and c not in cols:
                cols.append(c)
        return cols

    def scan(self, tab):
        res = tab.evaluate(SCAN_JS % (json.dumps(self.wf["row_selector"]), json.dumps(self._columns())))
        if res.get("error"):
            raise CDPError(res["error"])
        missing = res["missing"]
        if self.wf["status_column"] in missing:
            raise CDPError("Không thấy cột '%s' trên trang (kiểm tra tên cột, hoặc danh sách chưa tải xong)"
                           % self.wf["status_column"])
        rows = res["rows"]
        tag_col = self.wf["tag_column"] if self.wf["tag_column"] not in missing else ""
        for r in rows:
            r["status"] = r["cells"].get(self.wf["status_column"])
            r["tags"] = r["cells"].get(tag_col) if tag_col else r["text"]
            r["key"] = self._key(r)
            r["label"] = (r["cells"].get(self.wf["key_column"]) if self.wf["key_column"] else None) \
                or r["text"][:50]
        return rows

    def _key(self, r):
        if self.wf["key_column"]:
            k = r["cells"].get(self.wf["key_column"])
            if k:
                return norm(k)
        # không có cột mã: dùng chữ của cả dòng, bỏ chữ thẻ (vì thẻ thay đổi sau khi gắn)
        t = norm(r["text"])
        tag = norm(self.wf["tag"])
        return (t.replace(tag, " ") if tag else t)[:200].strip()

    def has_tag(self, r):
        tag = norm(self.wf["tag"])
        return bool(tag) and tag in norm(r.get("tags") or "")

    def decide(self, r):
        allowed = {norm(v) for v in self.wf["status_values"] if v and v.strip()}
        if r["status"] is None:
            return "skip", "không đọc được tình trạng"
        if allowed and norm(r["status"]) not in allowed:
            return "skip", "bỏ qua — tình trạng: %s" % (r["status"] or "(trống)")
        if self.has_tag(r):
            return "skip", "bỏ qua — đã có thẻ '%s'" % self.wf["tag"]
        entry = self.ledger.get(r["key"])
        if entry and entry.get("printed"):
            return "tag", "đã in lúc %s, cần gắn thẻ" % entry.get("time", "?")
        return "print", "cần in"

    def _wait_ready(self, tab, timeout=25):
        tab.wait_for_load(60)
        deadline = time.time() + timeout
        while True:
            try:
                rows = self.scan(tab)
                if rows:
                    return rows
                err = CDPError("Danh sách không có dòng nào khớp '%s'" % self.wf["row_selector"])
            except CDPError as e:
                err = e
            if time.time() > deadline or self.stop.is_set():
                raise err
            time.sleep(1)

    def _find(self, tab, key, timeout=10):
        """Tìm lại dòng theo mã (danh sách có thể đã vẽ lại) và đánh dấu nó."""
        deadline = time.time() + timeout
        while True:
            for r in self.scan(tab):
                if r["key"] == key:
                    if tab.evaluate(MARK_JS % (json.dumps(self.wf["row_selector"]), r["i"])):
                        return r
            if time.time() > deadline:
                raise CDPError("Không tìm lại được dòng chứng từ trên danh sách")
            time.sleep(0.7)

    # ----------------------------------------------------------- chạy
    def run(self, tab, dry_run=False):
        if dry_run:
            return self.run_once(tab, dry_run=True)
        ctx = capture.expect_pdf(self.cfg, self.log)
        with ctx:
            self.expect = ctx
            every = float(self.wf.get("every_minutes") or 0)
            while not self.stop.is_set():
                try:
                    self.run_once(tab)
                except CDPError as e:
                    if every <= 0:
                        raise
                    self.log("✖ %s" % e)
                if every <= 0:
                    break
                self.log("⏰ Sẽ chạy lại sau %g phút" % every)
                if self.stop.wait(every * 60):
                    break
                if not self.wf["list_url"]:
                    tab.reload()
        self.expect = None

    def run_once(self, tab, dry_run=False):
        wf = self.wf
        self.log("%s Quy trình '%s'%s" % ("🔍" if dry_run else "▶", wf["name"], " — CHẠY THỬ, không in" if dry_run else ""))
        if wf["list_url"]:
            tab.navigate(wf["list_url"])
        self._wait_ready(tab)
        done, stats, page, count = set(), {"print": 0, "tag": 0, "skip": 0, "ok": 0, "error": 0}, 1, 0
        while not self.stop.is_set():
            todo = []
            for r in self.scan(tab):
                if r["key"] in done:
                    continue
                r["action"], r["reason"] = self.decide(r)
                if r["action"] == "skip" or dry_run:
                    done.add(r["key"])
                    stats[r["action"]] += 1
                    self.on_row(dict(r, result=r["reason"] if r["action"] == "skip" else
                                     ("➜ sẽ in rồi gắn thẻ" if r["action"] == "print" else "➜ sẽ gắn thẻ (đã in trước đó)")))
                else:
                    todo.append(r)
            if todo:
                r = todo[0]
                done.add(r["key"])
                stats[r["action"]] += 1
                ok = self._process(tab, r)
                stats["ok" if ok else "error"] += 1
                count += 1
                if count >= int(wf["max_items"] or 100):
                    self.log("Đã đủ %d chứng từ cho lượt này" % count)
                    break
                continue
            if wf["next_page_selector"] and self._next_page(tab):
                page += 1
                self.log("→ Trang %d" % page)
                continue
            break
        if dry_run:
            self.log("🔍 Kết quả chạy thử: %d cần in, %d cần gắn thẻ lại, %d bỏ qua"
                     % (stats["print"], stats["tag"], stats["skip"]))
        else:
            self.log("✔ Xong lượt: %d thành công, %d lỗi, %d bỏ qua" % (stats["ok"], stats["error"], stats["skip"]))
        return stats

    def _next_page(self, tab):
        sel = self.wf["next_page_selector"]
        if not tab.evaluate(NEXT_JS % json.dumps(sel)):
            return False
        first = [r["key"] for r in self.scan(tab)][:1]
        tab.click(sel)
        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(0.8)
            try:
                rows = self.scan(tab)
            except CDPError:
                continue
            if rows and [rows[0]["key"]] != first:
                return True
        return False

    def _vars(self, r):
        return {"tag": self.wf["tag"], "key": r["label"], "status": r["status"] or ""}

    def _steplog(self, msg):
        self.log("   " + msg.strip())

    def _process(self, tab, r):
        wf, key = self.wf, r["key"]
        self.log("— %s [%s]" % (r["label"], r["status"]))
        try:
            if r["action"] == "print":
                job = self._print_row(tab, r)
                self.ledger.update(key, printed=True, job=job, tagged=False,
                                   time=time.strftime("%Y-%m-%d %H:%M:%S"), label=r["label"])
                if wf["wait_job"] and job:
                    self.log("   ⏳ Chờ máy in in xong %s..." % job)
                    if not printing.wait_job(job, float(wf["job_timeout"]), stop_event=self.stop):
                        return self._result(r, False, "⚠ đã gửi lệnh in %s nhưng máy in chưa in xong "
                                                      "(hết giấy/kẹt/tắt máy?) — chưa gắn thẻ" % job)
            else:
                job = (self.ledger.get(key) or {}).get("job")
                if wf["wait_job"] and job and not printing.wait_job(job, 0):
                    return self._result(r, False, "⏳ lệnh in %s vẫn còn trong hàng đợi — chưa gắn thẻ" % job)
            if not wf["tag_steps"]:
                return self._result(r, True, "✔ đã in")
            self._tag_row(tab, r)
            self.ledger.update(key, tagged=True)
            return self._result(r, True, ("✔ đã in và gắn thẻ '%s'" if r["action"] == "print" else
                                          "✔ đã gắn thẻ '%s' (đã in trước đó, không in lại)") % wf["tag"])
        except (CDPError, printing.PrintError, OSError) as e:
            return self._result(r, False, "✖ %s" % e)

    def _result(self, r, ok, msg):
        self.log("   " + msg)
        self.on_row(dict(r, result=msg))
        return ok

    def _print_row(self, tab, r):
        wf = self.wf
        self._find(tab, r["key"])
        self.expect.clear()
        before = {t["id"] for t in self.browser.tabs()}
        # Bước {"action": "wait_pdf"} chia đôi: trước nó = mở bản in, sau nó = dọn dẹp (đóng hộp thoại...)
        steps = wf["print_steps"]
        cut = next((i for i, s in enumerate(steps) if s.get("action") == "wait_pdf"), len(steps))
        open_steps, after_steps = steps[:cut], steps[cut + 1:]
        macro.run(tab, open_steps, self.cfg, self._vars(r), self._steplog, self.stop, row_scope=ROW_MARK,
                  refind_row=lambda: self._find(tab, r["key"]))
        info = new_tab = None
        new_since = None
        deadline = time.time() + float(wf["pdf_timeout"])
        while time.time() < deadline and not self.stop.is_set():
            try:
                info = self.expect.q.get(timeout=0.5)
                break
            except queue.Empty:
                pass
            new = [t for t in self.browser.tabs() if t["id"] not in before]
            if new:
                new_since = new_since or time.time()
                if time.time() - new_since > 5:   # có tab in mới nhưng không có PDF máy chủ
                    new_tab = new[-1]
                    break
        time.sleep(0.6)
        self.expect.clear()          # bỏ bản trùng (nếu thẻ "PDF máy chủ" cũng đang bật)
        printer = wf["printer"] or self.cfg.get("printer")
        copies = int(wf["copies"] or self.cfg.get("copies", 1))
        if info:
            job = capture.print_original(info["path"], self.cfg, title=info["name"], printer=printer, copies=copies)
            self.log("   🖨 In PDF gốc '%s' → %s" % (info["name"], job))
        elif new_tab:
            with self.browser.connect(new_tab) as t2:
                t2.wait_for_load(30)
                pdf, job = print_tab(t2, printer=printer, copies=copies, paper=self.cfg.get("paper", "A4"),
                                     landscape=self.cfg.get("landscape", False),
                                     margin_mm=self.cfg.get("margin_mm", 10), keep_pdf=True,
                                     fit_to_page=self.cfg.get("fit_to_page", True), log=self._steplog)
            self.log("   🖨 In trang '%s' → %s" % (new_tab.get("title", ""), job))
        else:
            raise CDPError("đã bấm In nhưng không thấy PDF hay trang in nào sau %ss" % wf["pdf_timeout"])
        if after_steps:
            try:
                macro.run(tab, after_steps, self.cfg, self._vars(r), self._steplog, self.stop, row_scope=ROW_MARK,
                  refind_row=lambda: self._find(tab, r["key"]))
            except CDPError as e:   # đã in rồi: lỗi dọn dẹp chỉ cảnh báo, bước gắn thẻ sẽ tự báo nếu bị che
                self.log("   ⚠ bước sau khi in: %s" % e)
        if wf["close_new_tabs"]:
            for t in self.browser.tabs():
                if t["id"] not in before:
                    try:
                        self.browser.close_tab(t["id"])
                    except CDPError:
                        pass
            try:
                self.browser.activate(tab.info.get("id", ""))
            except CDPError:
                pass
        return job

    def _tag_row(self, tab, r):
        wf = self.wf
        self._find(tab, r["key"])
        macro.run(tab, wf["tag_steps"], self.cfg, self._vars(r), self._steplog, self.stop, row_scope=ROW_MARK,
                  refind_row=lambda: self._find(tab, r["key"]))
        if not wf["verify_tag"]:
            return
        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(1)
            try:
                m = [x for x in self.scan(tab) if x["key"] == r["key"]]
            except CDPError:
                continue
            if m and self.has_tag(m[0]):
                return
        raise CDPError("đã thao tác gắn thẻ nhưng chưa thấy thẻ '%s' trên dòng — kiểm tra lại các bước gắn thẻ"
                       % wf["tag"])
