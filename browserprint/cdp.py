"""Điều khiển trình duyệt nhân Chromium (Chrome, Chromium, Edge, Brave, Cốc Cốc...)
qua Chrome DevTools Protocol (CDP).

Trình duyệt phải được mở với cờ --remote-debugging-port=<port>
(xem launcher.py).
"""

import base64
import itertools
import json
import threading
import time
import urllib.parse
import urllib.request

import websocket


class CDPError(Exception):
    pass


# Kích thước giấy (inch) cho Page.printToPDF
PAPER_SIZES = {
    "A4": (8.27, 11.69),
    "A5": (5.83, 8.27),
    "A3": (11.69, 16.54),
    "Letter": (8.5, 11.0),
    "Legal": (8.5, 14.0),
    "K80 (hóa đơn 80mm)": (3.15, 11.69),
    "K58 (hóa đơn 58mm)": (2.28, 11.69),
    "Theo CSS trang": None,
}

KEY_CODES = {
    "Enter": (13, "\r"),
    "Tab": (9, "\t"),
    "Escape": (27, ""),
    "Backspace": (8, ""),
    "Delete": (46, ""),
    "ArrowUp": (38, ""),
    "ArrowDown": (40, ""),
    "ArrowLeft": (37, ""),
    "ArrowRight": (39, ""),
    "Home": (36, ""),
    "End": (35, ""),
    "PageUp": (33, ""),
    "PageDown": (34, ""),
    "F5": (116, ""),
}


class Browser:
    """Kết nối tới một trình duyệt đang mở cổng remote debugging."""

    def __init__(self, host="127.0.0.1", port=9222):
        self.host = host
        self.port = int(port)

    def _http(self, path, method="GET"):
        url = "http://%s:%d%s" % (self.host, self.port, path)
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = resp.read().decode("utf-8")
        except OSError as e:
            raise CDPError(
                "Không kết nối được trình duyệt tại %s:%d (%s). "
                "Hãy mở trình duyệt bằng nút 'Mở trình duyệt' hoặc với cờ "
                "--remote-debugging-port=%d" % (self.host, self.port, e, self.port)
            )
        try:
            return json.loads(data)
        except ValueError:
            return data

    def is_alive(self):
        try:
            self._http("/json/version")
            return True
        except CDPError:
            return False

    def version(self):
        return self._http("/json/version")

    def tabs(self):
        """Danh sách tab (chỉ loại 'page')."""
        return [t for t in self._http("/json/list") if t.get("type") == "page"]

    def new_tab(self, url="about:blank"):
        return self._http("/json/new?" + urllib.parse.quote(url, safe=":/?&=#%"), method="PUT")

    def activate(self, tab_id):
        self._http("/json/activate/" + tab_id)

    def close_tab(self, tab_id):
        self._http("/json/close/" + tab_id)

    def connect(self, tab):
        """tab: dict từ tabs() hoặc id của tab."""
        if isinstance(tab, str):
            matches = [t for t in self.tabs() if t["id"] == tab]
            if not matches:
                raise CDPError("Không tìm thấy tab %s" % tab)
            tab = matches[0]
        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            raise CDPError(
                "Tab đang được DevTools khác điều khiển, hãy đóng cửa sổ DevTools rồi thử lại."
            )
        return Tab(ws_url, tab)

    def find_tab(self, url_contains=None, title_contains=None):
        for t in self.tabs():
            if url_contains and url_contains not in t.get("url", ""):
                continue
            if title_contains and title_contains not in t.get("title", ""):
                continue
            return t
        return None


class Tab:
    """Phiên điều khiển một tab qua WebSocket CDP."""

    def __init__(self, ws_url, info=None):
        self.info = info or {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        try:
            self.ws = websocket.create_connection(ws_url, timeout=60, suppress_origin=True)
        except Exception as e:
            raise CDPError("Không mở được kết nối tới tab: %s" % e)

    # ------------------------------------------------------------------ core
    def send(self, method, params=None, timeout=60):
        with self._lock:
            msg_id = next(self._ids)
            self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            deadline = time.time() + timeout
            while True:
                if time.time() > deadline:
                    raise CDPError("Hết thời gian chờ %s" % method)
                raw = self.ws.recv()
                msg = json.loads(raw)
                if msg.get("id") != msg_id:
                    continue  # bỏ qua sự kiện
                if "error" in msg:
                    raise CDPError("%s: %s" % (method, msg["error"].get("message")))
                return msg.get("result", {})

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------- helpers
    def evaluate(self, expression, await_promise=False):
        res = self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
            "userGesture": True,
        })
        if "exceptionDetails" in res:
            det = res["exceptionDetails"]
            text = det.get("exception", {}).get("description") or det.get("text")
            raise CDPError("Lỗi JavaScript: %s" % text)
        return res.get("result", {}).get("value")

    def url(self):
        return self.evaluate("location.href")

    def title(self):
        return self.evaluate("document.title")

    def wait_for_load(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.evaluate("document.readyState") == "complete":
                    return True
            except CDPError:
                pass
            time.sleep(0.3)
        raise CDPError("Trang chưa tải xong sau %ss" % timeout)

    def wait_for_selector(self, selector, timeout=30, visible=False):
        sel = json.dumps(selector)
        if visible:
            expr = ("(()=>{const e=document.querySelector(%s);if(!e)return false;"
                    "const r=e.getBoundingClientRect();return r.width>0&&r.height>0;})()" % sel)
        else:
            expr = "!!document.querySelector(%s)" % sel
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.evaluate(expr):
                    return True
            except CDPError:
                pass
            time.sleep(0.3)
        raise CDPError("Không thấy phần tử '%s' sau %ss" % (selector, timeout))

    def _require(self, selector):
        if not self.evaluate("!!document.querySelector(%s)" % json.dumps(selector)):
            raise CDPError("Không tìm thấy phần tử: %s" % selector)

    # ------------------------------------------------------------ actions
    def navigate(self, url, wait=True, timeout=30):
        if "://" not in url and not url.startswith("about:"):
            url = "https://" + url
        res = self.send("Page.navigate", {"url": url})
        if res.get("errorText"):
            raise CDPError("Không mở được %s: %s" % (url, res["errorText"]))
        if wait:
            time.sleep(0.5)
            self.wait_for_load(timeout)

    def reload(self, wait=True):
        self.send("Page.reload", {})
        if wait:
            time.sleep(0.5)
            self.wait_for_load()

    def click(self, selector):
        """Click như chuột thật (cuộn tới phần tử, bấm vào giữa)."""
        self._require(selector)
        rect = self.evaluate(
            "(()=>{const e=document.querySelector(%s);e.scrollIntoView({block:'center',inline:'center'});"
            "const r=e.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;"
            "const t=document.elementFromPoint(x,y);"
            # phần tử khác (lớp phủ, tiêu đề dính, cột lệch...) đang nằm trên điểm click?
            "const lab=t&&t.closest&&t.closest('label');"
            "const hit=!!t&&(t===e||e.contains(t)||t.contains(e)&&t.tagName==='LABEL'||(lab&&lab.control===e));"
            "return {x,y,w:r.width,h:r.height,hit,hidden:document.visibilityState==='hidden'};})()"
            % json.dumps(selector))
        if not rect or rect["w"] == 0 or rect["h"] == 0 or rect["hidden"] or not rect["hit"]:
            # phần tử ẩn, bị phần tử khác che, hoặc cửa sổ trình duyệt đang thu nhỏ (sự kiện chuột
            # không tới): dùng click() của DOM — vẫn tính là thao tác người dùng nên mở được popup/tab mới
            self.evaluate("document.querySelector(%s).click()" % json.dumps(selector))
            return
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", {
                "type": typ, "x": rect["x"], "y": rect["y"],
                "button": "left", "clickCount": 1,
            })

    def find_text(self, text, scope=None, exact=False):
        """Đánh dấu phần tử hiển thị có chữ `text` (ưu tiên khớp đúng, phần tử nhỏ nhất,
        ưu tiên trong hộp thoại đang mở). Trả về chữ của phần tử, hoặc None."""
        return self.evaluate("""(()=>{
            const norm = s => (s || '').normalize('NFC').replace(/\\s+/g, ' ').trim().toLowerCase();
            const T = norm(%s), SCOPE = %s, EXACT = %s;
            const vis = e => { const r = e.getBoundingClientRect(); if (!(r.width > 0 && r.height > 0)) return false;
                const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none'; };
            document.querySelectorAll('[data-bp-target]').forEach(e => e.removeAttribute('data-bp-target'));
            let roots;
            if (SCOPE) { const s = document.querySelector(SCOPE); if (!s) return null; roots = [s]; }
            else {
                const dlg = [...document.querySelectorAll('[role=dialog],[role=menu],[role=listbox],[aria-modal=true],.modal.show,.dx-overlay-content,.popup,.dropdown-menu.show')].filter(vis);
                roots = dlg.length ? [dlg[dlg.length - 1], document] : [document];
            }
            for (const root of roots) {
                let best = null, bestScore = Infinity;
                for (const el of root.querySelectorAll('*')) {
                    const raw = el.textContent || el.value || el.getAttribute('aria-label') || el.title || '';
                    if (!norm(raw).includes(T)) continue;            // lọc nhanh
                    if (!vis(el)) continue;
                    const t = norm(el.innerText || el.value || el.getAttribute('aria-label') || el.title);
                    const exact = t === T;
                    if (!exact && (EXACT || !t.includes(T))) continue;
                    const r = el.getBoundingClientRect();
                    const score = (exact ? 0 : 1e12) + r.width * r.height;
                    if (score < bestScore) { best = el; bestScore = score; }
                }
                if (best) {
                    const target = best.closest('button,a,[role=button],[role=menuitem],[role=option],[role=tab],[role=checkbox],li,label') || best;
                    target.setAttribute('data-bp-target', '1');
                    return (target.innerText || target.value || T).trim().slice(0, 80);
                }
            }
            return null;
        })()""" % (json.dumps(text), json.dumps(scope), "true" if exact else "false"))

    def wait_text(self, text, timeout=15, scope=None, exact=False):
        deadline = time.time() + timeout
        while True:
            found = self.find_text(text, scope, exact)
            if found is not None:
                return found
            if time.time() > deadline:
                raise CDPError("Không thấy chữ '%s' trên trang sau %ss" % (text, timeout))
            time.sleep(0.4)

    def click_text(self, text, timeout=15, scope=None, exact=False):
        """Click vào nút/mục có chữ `text` — bền hơn CSS selector với web hay đổi giao diện."""
        self.wait_text(text, timeout, scope, exact)
        self.click('[data-bp-target="1"]')

    def fill(self, selector, value):
        """Điền giá trị vào ô input/textarea (tương thích React/Vue/Angular)."""
        self._require(selector)
        self.evaluate("""(()=>{
            const e=document.querySelector(%s); e.focus();
            const proto = e instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto,'value');
            if (setter && setter.set && !e.isContentEditable) setter.set.call(e, %s);
            else if (e.isContentEditable) e.innerText = %s; else e.value = %s;
            e.dispatchEvent(new Event('input',{bubbles:true}));
            e.dispatchEvent(new Event('change',{bubbles:true}));
        })()""" % (json.dumps(selector), json.dumps(value), json.dumps(value), json.dumps(value)))

    def select(self, selector, value):
        """Chọn option trong <select> theo value hoặc theo chữ hiển thị."""
        self._require(selector)
        ok = self.evaluate("""(()=>{
            const e=document.querySelector(%s); const v=%s;
            const o=[...e.options].find(o=>o.value===v||o.text.trim()===v);
            if(!o) return false; e.value=o.value;
            e.dispatchEvent(new Event('input',{bubbles:true}));
            e.dispatchEvent(new Event('change',{bubbles:true})); return true;
        })()""" % (json.dumps(selector), json.dumps(value)))
        if not ok:
            raise CDPError("Không có lựa chọn '%s' trong %s" % (value, selector))

    def check(self, selector, checked=True):
        self._require(selector)
        cur = self.evaluate("document.querySelector(%s).checked" % json.dumps(selector))
        if bool(cur) != bool(checked):
            self.click(selector)

    def focus(self, selector):
        self._require(selector)
        self.evaluate("document.querySelector(%s).focus()" % json.dumps(selector))

    def type_text(self, text):
        """Gõ chữ vào phần tử đang được focus."""
        self.send("Input.insertText", {"text": text})

    def press(self, key):
        code, text = KEY_CODES.get(key, (0, key if len(key) == 1 else ""))
        base = {"key": key, "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
        down = dict(base, type="keyDown")
        if text:
            down["text"] = text
        self.send("Input.dispatchKeyEvent", down)
        self.send("Input.dispatchKeyEvent", dict(base, type="keyUp"))

    def get_text(self, selector):
        self._require(selector)
        return self.evaluate(
            "(()=>{const e=document.querySelector(%s);return ('value' in e && e.value!==undefined && e.tagName!=='BUTTON')?e.value:e.innerText;})()"
            % json.dumps(selector))

    def screenshot(self, path, full_page=False):
        params = {"format": "png"}
        if full_page:
            m = self.send("Page.getLayoutMetrics")
            size = m.get("cssContentSize") or m.get("contentSize")
            params["clip"] = {"x": 0, "y": 0, "width": size["width"], "height": size["height"], "scale": 1}
            params["captureBeyondViewport"] = True
        data = self.send("Page.captureScreenshot", params)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    # ------------------------------------------------ PDF do máy chủ tạo
    def pdf_source(self, allow_embedded=True):
        """Nếu tab đang hiển thị file PDF (trình xem PDF của trình duyệt) thì trả về URL
        của file đó; allow_embedded: tìm cả PDF nhúng trong <embed>/<iframe>/<object>."""
        return self.evaluate("""(()=>{
            const isPdf = (d) => { try { return d && d.contentType === 'application/pdf'; } catch (e) { return false; } };
            if (isPdf(document)) return location.href;
            if (!%s) return null;
            for (const e of document.querySelectorAll('embed,object,iframe')) {
                const src = e.src || e.data || '';
                if (!src || src === 'about:blank') continue;
                if ((e.type || '').toLowerCase() === 'application/pdf') return src;
                if (/\\.pdf([?#]|$)/i.test(src)) return src;
                if (e.tagName === 'IFRAME' && isPdf(e.contentDocument)) return src;
            }
            return null;
        })()""" % ("true" if allow_embedded else "false"))

    def fetch_bytes(self, url):
        """Tải lại một URL ngay trong tab (dùng cookie/phiên đăng nhập của trang)."""
        res = self.evaluate("""(async()=>{
            const u = new URL(%s, location.href);
            // đánh dấu để bộ bắt PDF (capture.py) không bắt lại yêu cầu này -> tránh in 2 lần
            const headers = u.origin === location.origin ? {'X-Browser-Print': '1'} : {};
            const r = await fetch(u.href, {credentials: 'include', headers});
            if (!r.ok) throw new Error('HTTP ' + r.status);
            const b = new Uint8Array(await r.arrayBuffer()); let s = '';
            for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
            return {type: r.headers.get('content-type') || '', b64: btoa(s)};
        })()""" % json.dumps(url), await_promise=True)
        return base64.b64decode(res["b64"])

    # ------------------------------------------------------------ printing
    def print_pdf(self, path, paper="A4", landscape=False, selector=None,
                  margin_mm=10, background=True, scale=1.0, page_ranges=""):
        """Xuất trang (hoặc chỉ phần tử selector) ra PDF.

        selector: chỉ in vùng chứng từ (ví dụ '#invoice'), ẩn menu/thanh công cụ.
        """
        style_id = "__browser_print_style__"
        if selector:
            self._require(selector)
            css = ("@media print{body *{visibility:hidden!important}"
                   "%s,%s *{visibility:visible!important}"
                   "%s{position:absolute!important;left:0!important;top:0!important;width:100%%!important;margin:0!important}}"
                   % (selector, selector, selector))
            self.evaluate("""(()=>{let s=document.getElementById(%s);
                if(!s){s=document.createElement('style');s.id=%s;document.head.appendChild(s);}
                s.textContent=%s;})()""" % (json.dumps(style_id), json.dumps(style_id), json.dumps(css)))
        try:
            margin = margin_mm / 25.4
            params = {
                "landscape": bool(landscape),
                "printBackground": bool(background),
                "scale": float(scale),
                "marginTop": margin, "marginBottom": margin,
                "marginLeft": margin, "marginRight": margin,
                "pageRanges": page_ranges or "",
            }
            size = PAPER_SIZES.get(paper)
            if size is None:
                params["preferCSSPageSize"] = True
            else:
                params["paperWidth"], params["paperHeight"] = size
            data = self.send("Page.printToPDF", params, timeout=120)["data"]
        finally:
            if selector:
                try:
                    self.evaluate("(()=>{const s=document.getElementById(%s);if(s)s.remove();})()"
                                  % json.dumps(style_id))
                except CDPError:
                    pass
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path
