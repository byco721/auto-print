"""Bắt file PDF do máy chủ tạo ra (Server-side Document Generation).

Các web như MISA, hóa đơn điện tử, ngân hàng... thường tạo chứng từ PDF ở máy chủ
rồi trả về cho trình duyệt theo một trong các cách:
  - mở trong tab mới (trình xem PDF), kể cả qua form POST hoặc link dùng một lần;
  - tải về (Content-Disposition: attachment);
  - gọi XHR/fetch lấy PDF rồi hiển thị qua blob: URL.

Module này kết nối ở mức trình duyệt, tự gắn vào mọi tab/iframe (kể cả tab mới mở,
trước khi tab kịp tải), chặn các phản hồi ở bước Response bằng CDP Fetch domain và
lưu lại phần thân của những phản hồi là PDF — đúng file gốc máy chủ gửi về.
"""

import base64
import hashlib
import itertools
import json
import os
import re
import threading
import time
import urllib.parse

import websocket

from . import printing
from .cdp import Browser, CDPError
from .config import PDF_DIR

CAPTURE_DIR = os.path.join(PDF_DIR, "server")
PDF_TYPES = ("application/pdf", "application/x-pdf", "application/acrobat")

# url -> đường dẫn file: PDF bắt được gần đây, để in lại các chứng từ không tải lại được
_recent = {}
_recent_lock = threading.Lock()

# Các bộ bắt đang chạy trong tiến trình, và các bên đang "chờ PDF" (In theo điều kiện).
# Khi có bên chờ, PDF được giao cho bên đó và KHÔNG tự in, tránh in 2 lần.
_active = set()
_listeners = []
_hooks_lock = threading.Lock()


class expect_pdf:
    """Ngữ cảnh nhận PDF do máy chủ trả về:

        with expect_pdf(cfg, log) as q:
            ...bấm nút In...
            info = q.get(timeout=60)

    Luôn chạy một bộ bắt riêng (bắt mọi PDF, không tự in). Nếu thẻ "PDF máy chủ" cũng đang
    bật thì bộ đó cũng giao PDF vào đây và tạm không tự in."""

    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.own = None
        import queue
        self.q = queue.Queue()

    def __enter__(self):
        with _hooks_lock:
            _listeners.append(self.q.put)
        cfg = dict(self.cfg, capture=dict(self.cfg.get("capture", {}), auto_print=False, url_pattern=""))
        self.own = PdfCapture(cfg, log=lambda m: None)
        self.own.start()
        if not self.own.ready.wait(15):
            self.__exit__()
            raise CDPError("Không khởi động được bộ bắt PDF (trình duyệt chưa mở cổng điều khiển?)")
        return self.q

    def __exit__(self, *exc):
        with _hooks_lock:
            if self.q.put in _listeners:
                _listeners.remove(self.q.put)
        if self.own:
            self.own.stop()

    def clear(self):
        while not self.q.empty():
            self.q.get_nowait()


def remember(url, path):
    if not url:
        return
    with _recent_lock:
        _recent.pop(url, None)
        _recent[url] = path
        while len(_recent) > 300:
            _recent.pop(next(iter(_recent)))


def recent_for(url):
    with _recent_lock:
        path = _recent.get(url)
    return path if path and os.path.exists(path) else None


def _headers(lst):
    return {h["name"].lower(): h["value"] for h in lst or []}


def is_pdf_response(headers, url):
    ct = headers.get("content-type", "").lower()
    if any(t in ct for t in PDF_TYPES):
        return True
    if ".pdf" in headers.get("content-disposition", "").lower():
        return True
    return "octet-stream" in ct and urllib.parse.urlsplit(url).path.lower().endswith(".pdf")


def filename_from(headers, url):
    cd = headers.get("content-disposition", "")
    m = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", cd, re.I)
    if m:
        name = urllib.parse.unquote(m.group(1).strip().strip('"'))
    else:
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
        name = m.group(1).strip() if m else urllib.parse.unquote(
            os.path.basename(urllib.parse.urlsplit(url).path))
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .") or "chung-tu"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name[-120:]


class PdfCapture:
    def __init__(self, cfg, on_pdf=None, log=print):
        self.cfg = cfg
        self.on_pdf = on_pdf
        self.log = log
        self.ws = None
        self._stop = threading.Event()
        self._thread = None
        self._ids = itertools.count(1)
        self._send_lock = threading.Lock()
        self._pending = {}
        self._sessions = {}     # sessionId -> targetInfo
        self._seen = {}         # sha1 -> (thời điểm, file), chống trùng
        self._seen_lock = threading.Lock()
        self.ready = threading.Event()   # đã gắn xong vào các tab

    @property
    def opts(self):
        return self.cfg.setdefault("capture", {})

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self.ready.clear()
        with _hooks_lock:
            _active.add(self)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="pdf-capture")
        self._thread.start()

    def stop(self):
        self._stop.set()
        with _hooks_lock:
            _active.discard(self)
        ws, self.ws = self.ws, None
        if ws:
            try:
                ws.close()   # ngắt kết nối thì trình duyệt tự bỏ chặn mọi yêu cầu
            except Exception:
                pass
        self.log("■ Dừng bắt PDF từ máy chủ")

    # ------------------------------------------------------------ giao tiếp
    def _send(self, method, params=None, session=None, wait=True, timeout=30):
        ws = self.ws
        if ws is None:
            raise CDPError("Chưa kết nối")
        msg_id = next(self._ids)
        msg = {"id": msg_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        slot = [threading.Event(), None]
        if wait:
            self._pending[msg_id] = slot
        with self._send_lock:
            ws.send(json.dumps(msg))
        if not wait:
            return None
        if not slot[0].wait(timeout):
            self._pending.pop(msg_id, None)
            raise CDPError("Hết thời gian chờ %s" % method)
        if "error" in slot[1]:
            raise CDPError("%s: %s" % (method, slot[1]["error"].get("message")))
        return slot[1].get("result", {})

    def _loop(self):
        warned = False
        while not self._stop.is_set():
            try:
                self._run_once()
                warned = False
            except Exception as e:
                if self._stop.is_set():
                    break
                if not warned:
                    self.log("⚠ Bắt PDF: %s — sẽ tự kết nối lại" % e)
                    warned = True
            self.ws = None
            self._stop.wait(3)

    def _run_once(self):
        self.ready.clear()
        info = Browser(self.cfg["host"], self.cfg["port"]).version()
        self.ws = websocket.create_connection(info["webSocketDebuggerUrl"], suppress_origin=True)
        self.ws.settimeout(1.0)
        self._sessions.clear()
        threading.Thread(target=self._setup, daemon=True).start()
        while not self._stop.is_set():
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except (AttributeError, OSError):
                raise CDPError("mất kết nối trình duyệt")
            if not raw:
                raise CDPError("mất kết nối trình duyệt")
            msg = json.loads(raw)
            if "id" in msg:
                slot = self._pending.pop(msg["id"], None)
                if slot:
                    slot[1] = msg
                    slot[0].set()
            else:
                self._dispatch(msg)

    def _setup(self):
        try:
            # Tự gắn vào mọi tab mới; tab mới sẽ tạm dừng cho đến khi đã bật chặn.
            self._send("Target.setAutoAttach", {
                "autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True,
                "filter": [{"type": "page", "exclude": False}],
            })
            time.sleep(0.5)
            attached = {ti.get("targetId") for ti in list(self._sessions.values())}
            for t in self._send("Target.getTargets")["targetInfos"]:
                if t["type"] == "page" and t["targetId"] not in attached:
                    try:
                        self._send("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})
                    except CDPError:
                        pass
            n = sum(1 for ti in list(self._sessions.values()) if ti.get("type") == "page")
            time.sleep(0.3)   # chờ các tab vừa gắn bật xong chế độ chặn
            self.ready.set()
            self.log("▶ Đang bắt PDF từ máy chủ (%d tab)" % n)
        except Exception as e:
            self.log("⚠ Bắt PDF: không khởi tạo được: %s" % e)

    def _dispatch(self, msg):
        method = msg.get("method")
        p = msg.get("params", {})
        if method == "Target.attachedToTarget":
            sid, ti = p["sessionId"], p["targetInfo"]
            self._sessions[sid] = ti
            threading.Thread(target=self._init_session, args=(sid, ti, p.get("waitingForDebugger")),
                             daemon=True).start()
        elif method == "Target.detachedFromTarget":
            self._sessions.pop(p.get("sessionId"), None)
        elif method == "Fetch.requestPaused":
            threading.Thread(target=self._on_paused, args=(msg.get("sessionId"), p), daemon=True).start()

    def _init_session(self, sid, ti, waiting):
        try:
            if ti.get("type") not in ("page", "iframe"):
                if waiting:
                    self._send("Runtime.runIfWaitingForDebugger", session=sid, wait=False)
                self._send("Target.detachFromTarget", {"sessionId": sid}, wait=False)
                return
            patterns = [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Response"}]
            if self.opts.get("include_xhr", True):
                patterns += [{"urlPattern": "*", "resourceType": t, "requestStage": "Response"}
                             for t in ("XHR", "Fetch", "Other")]
            self._send("Fetch.enable", {"patterns": patterns}, session=sid)
            # gắn tiếp vào iframe khác domain (OOPIF) bên trong tab
            self._send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True,
                                                "flatten": True}, session=sid)
        except Exception:
            pass
        finally:
            if waiting:
                try:
                    self._send("Runtime.runIfWaitingForDebugger", session=sid, wait=False)
                except Exception:
                    pass

    def _page_info(self, sid):
        ti = self._sessions.get(sid) or {}
        try:
            return self._send("Target.getTargetInfo", {"targetId": ti["targetId"]})["targetInfo"]
        except Exception:
            return ti

    def _wanted(self, *texts):
        pat = (self.opts.get("url_pattern") or "").strip()
        if not pat:
            return True
        try:
            return any(t and re.search(pat, t, re.I) for t in texts)
        except re.error:
            return True

    def _on_paused(self, sid, p):
        rid = p["requestId"]
        url = p.get("request", {}).get("url", "")
        data = headers = page = None
        try:
            headers = _headers(p.get("responseHeaders"))
            own = any(k.lower() == "x-browser-print" for k in p.get("request", {}).get("headers", {}))
            if not own and p.get("responseStatusCode") == 200 and is_pdf_response(headers, url):
                page = self._page_info(sid)
                if self._wanted(url, page.get("url"), page.get("title")):
                    body = self._send("Fetch.getResponseBody", {"requestId": rid}, session=sid, timeout=120)
                    raw = body.get("body", "")
                    data = base64.b64decode(raw) if body.get("base64Encoded") else raw.encode("latin-1")
        except Exception as e:
            self.log("⚠ Không lấy được PDF %s: %s" % (url[:80], e))
        finally:
            try:
                self._send("Fetch.continueRequest", {"requestId": rid}, session=sid, wait=False)
            except Exception:
                pass
        if data and data[:5] == b"%PDF-":
            self._save(data, url, headers, page or {})

    def _save(self, data, url, headers, page):
        digest = hashlib.sha1(data).hexdigest()
        now = time.time()
        name = filename_from(headers, url)
        path = os.path.join(CAPTURE_DIR, "%s_%s" % (time.strftime("%Y%m%d-%H%M%S"), name))
        with self._seen_lock:
            self._seen = {k: v for k, v in self._seen.items() if now - v[0] < 20}
            dup = self._seen.get(digest)
            if dup:
                # cùng một file vừa bắt (vd trình xem PDF tải lại): chỉ ghi nhớ URL, không in lại
                remember(url, dup[1])
                return
            self._seen[digest] = (now, path)
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        remember(url, path)
        info = {"time": time.strftime("%H:%M:%S"), "name": name, "path": path, "url": url,
                "size": len(data), "tab_url": page.get("url", ""), "tab_title": page.get("title", ""),
                "job": None}
        self.log("📥 Bắt được PDF '%s' (%d KB) từ %s" % (name, len(data) // 1024, url[:80]))
        with _hooks_lock:
            listeners = list(_listeners)
        for deliver in listeners:
            deliver(info)
        # đã có bên đang chờ PDF này (In theo điều kiện) thì không tự in nữa
        if not listeners and self.opts.get("auto_print"):
            try:
                info["job"] = print_original(path, self.cfg, title=name)
                self.log("🖨 Đã in PDF gốc '%s' → %s" % (name, info["job"]))
            except printing.PrintError as e:
                self.log("✖ In '%s' lỗi: %s" % (name, e))
        if self.on_pdf:
            self.on_pdf(info)


def print_original(path, cfg, title=None, printer=None, copies=None):
    """In file PDF gốc (không dựng lại) với cài đặt máy in trong cfg."""
    opts = {"fit-to-page": True} if cfg.get("fit_to_page", True) else {}
    return printing.print_file(path, printer=printer or cfg.get("printer") or None,
                               copies=copies or cfg.get("copies", 1), options=opts, title=title)
