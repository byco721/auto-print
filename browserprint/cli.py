"""Dòng lệnh: browser-print [gui|tabs|print|watch|macro|launch|printers]"""

import argparse
import signal
import sys
import time

from . import __version__, config, launcher, macro, printing
from .capture import PdfCapture
from .cdp import PAPER_SIZES, Browser, CDPError
from .jobs import print_tab
from .watcher import Watcher
from .workflow import WorkflowRunner, inspect, inspect_report


def _pick_tab(br, args):
    tabs = br.tabs()
    if not tabs:
        raise CDPError("Trình duyệt không có tab nào")
    if getattr(args, "url", None):
        for t in tabs:
            if args.url in t.get("url", ""):
                return t
        raise CDPError("Không có tab nào có URL chứa '%s'" % args.url)
    if getattr(args, "title", None):
        for t in tabs:
            if args.title.lower() in t.get("title", "").lower():
                return t
        raise CDPError("Không có tab nào có tiêu đề chứa '%s'" % args.title)
    idx = getattr(args, "index", 0) or 0
    if idx >= len(tabs):
        raise CDPError("Chỉ có %d tab" % len(tabs))
    return tabs[idx]


def main(argv=None):
    cfg = config.load()
    p = argparse.ArgumentParser(prog="browser-print",
                                description="Xem trình duyệt, in chứng từ và thao tác tự động.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--host", default=cfg["host"])
    p.add_argument("--port", type=int, default=cfg["port"])
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("gui", help="Mở giao diện (mặc định)")
    sub.add_parser("tabs", help="Liệt kê các tab đang mở")
    sub.add_parser("printers", help="Liệt kê máy in")

    lp = sub.add_parser("launch", help="Mở trình duyệt với cổng điều khiển")
    lp.add_argument("--browser", default=cfg.get("browser_path"))
    lp.add_argument("url", nargs="?")

    def tab_args(sp):
        sp.add_argument("-i", "--index", type=int, default=0, help="Số thứ tự tab (từ 0)")
        sp.add_argument("-u", "--url", help="Chọn tab có URL chứa chuỗi này")
        sp.add_argument("-t", "--title", help="Chọn tab có tiêu đề chứa chuỗi này")

    pp = sub.add_parser("print", help="In một tab")
    tab_args(pp)
    pp.add_argument("-P", "--printer", default=cfg.get("printer"))
    pp.add_argument("-n", "--copies", type=int, default=cfg.get("copies", 1))
    pp.add_argument("-s", "--selector", help="Chỉ in vùng này (CSS selector)")
    pp.add_argument("--paper", default=cfg.get("paper", "A4"), choices=list(PAPER_SIZES))
    pp.add_argument("--landscape", action="store_true")
    pp.add_argument("--pdf", help="Chỉ lưu PDF ra file này, không in")

    mp = sub.add_parser("macro", help="Chạy macro JSON trên một tab")
    tab_args(mp)
    mp.add_argument("file")
    mp.add_argument("-v", "--var", action="append", default=[], help="Biến dạng ten=giatri")

    wp = sub.add_parser("watch", help="Chạy nền: tự in theo các quy tắc đã cấu hình")
    wp.add_argument("--capture", action="store_true",
                    help="Bật thêm bắt PDF do máy chủ tạo (mặc định theo cấu hình)")

    ip = sub.add_parser("inspect", help="Tự nhận diện danh sách chứng từ trên một tab (cột, dòng, nút)")
    tab_args(ip)
    ip.add_argument("-c", "--column", default="Tình trạng ghi doanh số", help="Tên cột điều kiện")

    fp = sub.add_parser("workflow", help="In theo điều kiện rồi gắn thẻ (quy trình đã lưu trong giao diện)")
    tab_args(fp)
    fp.add_argument("-f", "--file", help="File JSON quy trình (mặc định: quy trình đã lưu)")
    fp.add_argument("--dry-run", action="store_true", help="Chỉ liệt kê, không in, không gắn thẻ")
    fp.add_argument("--every", type=float, default=None, help="Chạy lại mỗi N phút")

    cp = sub.add_parser("capture", help="Bắt (và tự in) file PDF do máy chủ tạo, vd MISA")
    cp.add_argument("-f", "--filter", default=None, help="Chỉ bắt PDF có URL/trang khớp regex này")
    cp.add_argument("--no-print", action="store_true", help="Chỉ lưu, không tự in")
    cp.add_argument("-P", "--printer", default=None)
    cp.add_argument("-n", "--copies", type=int, default=None)

    args = p.parse_args(argv)
    cfg["host"], cfg["port"] = args.host, args.port
    cmd = args.cmd or "gui"

    if cmd == "gui":
        from .gui import main as gui_main
        return gui_main()

    try:
        br = Browser(args.host, args.port)
        if cmd == "tabs":
            for i, t in enumerate(br.tabs()):
                print("%2d  %s\n    %s" % (i, t.get("title", ""), t.get("url", "")))
        elif cmd == "printers":
            default = printing.default_printer()
            for name in printing.list_printers():
                print(("* " if name == default else "  ") + name)
        elif cmd == "launch":
            launcher.launch(args.browser or None, args.port, cfg.get("profile_dir"), args.url, args.host)
            print("Trình duyệt sẵn sàng tại cổng %d" % args.port)
        elif cmd == "print":
            with br.connect(_pick_tab(br, args)) as tab:
                pdf, job = print_tab(tab, printer=args.printer, copies=args.copies, paper=args.paper,
                                     landscape=args.landscape, selector=args.selector,
                                     margin_mm=cfg.get("margin_mm", 10), keep_pdf=cfg.get("keep_pdf", True),
                                     pdf_path=args.pdf, send_to_printer=not args.pdf,
                                     fit_to_page=cfg.get("fit_to_page", True), log=print)
                print("PDF: %s" % pdf + ("\nLệnh in: %s" % job if job else ""))
        elif cmd == "macro":
            variables = dict(v.split("=", 1) for v in args.var if "=" in v)
            with br.connect(_pick_tab(br, args)) as tab:
                macro.run(tab, macro.load(args.file), cfg, variables)
        elif cmd == "inspect":
            with br.connect(_pick_tab(br, args)) as tab:
                print(inspect_report(inspect(tab, args.column), args.column))
        elif cmd == "workflow":
            wf = macro.load(args.file) if args.file else cfg.get("workflow")
            if not wf:
                raise ValueError("Chưa có quy trình. Hãy tạo ở thẻ 'In theo điều kiện' hoặc dùng --file")
            if args.every is not None:
                wf["every_minutes"] = args.every
            with br.connect(_pick_tab(br, args)) as tab:
                WorkflowRunner(cfg, wf, on_row=lambda r: print("  %-20s %-14s %s" % (
                    r["label"][:20], r.get("status") or "", r["result"]))).run(tab, dry_run=args.dry_run)
        elif cmd in ("watch", "capture"):
            workers = []
            if cmd == "capture":
                if args.filter is not None:
                    cfg["capture"]["url_pattern"] = args.filter
                cfg["capture"]["auto_print"] = not args.no_print
                if args.printer:
                    cfg["printer"] = args.printer
                if args.copies:
                    cfg["copies"] = args.copies
            else:
                workers.append(Watcher(cfg))
            if cmd == "capture" or args.capture or cfg["capture"].get("enabled"):
                workers.append(PdfCapture(cfg))
            for w in workers:
                w.start()
            signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
            try:
                while True:
                    time.sleep(1)
            except (KeyboardInterrupt, SystemExit):
                for w in workers:
                    w.stop()
    except (CDPError, printing.PrintError, OSError, ValueError) as e:
        print("Lỗi: %s" % e, file=sys.stderr)
        return 1
    return 0
