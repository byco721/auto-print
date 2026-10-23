"""Theo dõi các tab trình duyệt và tự động in khi URL/tiêu đề khớp quy tắc."""

import re
import threading
import time

from .cdp import Browser, CDPError
from .jobs import print_tab
from .printing import PrintError


class Watcher:
    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self._stop = threading.Event()
        self._thread = None
        # (tab id, url) đã in -> không in lại cho đến khi tab chuyển sang URL khác
        self._done = set()
        self._busy = set()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watcher")
        self._thread.start()
        self.log("▶ Bắt đầu theo dõi trình duyệt (%d quy tắc)" % len(self._rules()))

    def stop(self):
        self._stop.set()
        self.log("■ Dừng theo dõi")

    def reset(self):
        self._done.clear()

    def _rules(self):
        return [r for r in self.cfg.get("rules", []) if r.get("enabled") and
                (r.get("url_pattern") or r.get("title_pattern"))]

    @staticmethod
    def match(rule, tab):
        try:
            if rule.get("url_pattern") and not re.search(rule["url_pattern"], tab.get("url", ""), re.I):
                return False
            if rule.get("title_pattern") and not re.search(rule["title_pattern"], tab.get("title", ""), re.I):
                return False
        except re.error:
            return False
        return True

    def _loop(self):
        warned = False
        # Các tab đang mở sẵn lúc bắt đầu theo dõi thì coi như đã xử lý,
        # tránh in lại hàng loạt chứng từ cũ.
        try:
            br = Browser(self.cfg["host"], self.cfg["port"])
            for t in br.tabs():
                self._done.add((t["id"], t.get("url")))
        except CDPError:
            pass
        while not self._stop.is_set():
            try:
                br = Browser(self.cfg["host"], self.cfg["port"])
                tabs = br.tabs()
                warned = False
                open_ids = {t["id"] for t in tabs}
                self._done = {d for d in self._done if d[0] in open_ids}
                for t in tabs:
                    key = (t["id"], t.get("url"))
                    if key in self._done or t["id"] in self._busy:
                        continue
                    for rule in self._rules():
                        if self.match(rule, t):
                            self._done.add(key)
                            self._busy.add(t["id"])
                            threading.Thread(target=self._handle, args=(br, t, rule),
                                             daemon=True).start()
                            break
            except CDPError as e:
                if not warned:
                    self.log("⚠ %s" % e)
                    warned = True
            except Exception as e:  # không để luồng theo dõi chết
                self.log("⚠ Lỗi theo dõi: %s" % e)
            self._stop.wait(float(self.cfg.get("watch_interval", 2.0)))

    def _handle(self, br, t, rule):
        name = rule.get("name") or rule.get("url_pattern")
        try:
            with br.connect(t) as tab:
                tab.wait_for_load(60)
                if rule.get("wait_selector"):
                    tab.wait_for_selector(rule["wait_selector"], 60, visible=True)
                if rule.get("selector"):
                    tab.wait_for_selector(rule["selector"], 60)
                time.sleep(float(rule.get("delay") or 0))
                # URL có thể đã đổi trong lúc chờ
                t = dict(t, url=tab.url(), title=tab.title())
                if not self.match(rule, t):
                    self.log("… Bỏ qua '%s': trang đã chuyển đi" % name)
                    return
                pdf, job = print_tab(
                    tab,
                    printer=rule.get("printer") or self.cfg.get("printer"),
                    copies=rule.get("copies", 1),
                    paper=rule.get("paper", "A4"),
                    landscape=rule.get("landscape", False),
                    selector=rule.get("selector"),
                    margin_mm=self.cfg.get("margin_mm", 10),
                    keep_pdf=self.cfg.get("keep_pdf", True),
                    fit_to_page=self.cfg.get("fit_to_page", True),
                    log=self.log,
                )
                self.log("🖨 [%s] Đã in '%s' → %s (%s)" % (name, t.get("title", ""), job, pdf))
            if rule.get("close_tab"):
                br.close_tab(t["id"])
        except (CDPError, PrintError, OSError) as e:
            self.log("✖ [%s] Lỗi in '%s': %s" % (name, t.get("title", ""), e))
            # cho phép thử lại ở lượt sau
            self._done.discard((t["id"], t.get("url")))
        finally:
            self._busy.discard(t["id"])
