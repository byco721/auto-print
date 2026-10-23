"""In file PDF qua CUPS (lệnh lp / lpstat)."""

import re
import shutil
import subprocess
import time


class PrintError(Exception):
    pass


def _run(args):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise PrintError("Chưa cài CUPS. Chạy: sudo apt install cups cups-client")
    except subprocess.TimeoutExpired:
        raise PrintError("Lệnh %s chạy quá lâu" % args[0])
    return out


def cups_available():
    return shutil.which("lp") is not None


def list_printers():
    """Danh sách tên máy in đã cấu hình trong CUPS."""
    out = _run(["lpstat", "-e"])
    names = [l.strip() for l in out.stdout.splitlines() if l.strip()]
    if not names:
        out = _run(["lpstat", "-p"])
        names = [m.group(1) for m in re.finditer(r"^printer\s+(\S+)", out.stdout, re.M)]
    return names


def default_printer():
    out = _run(["lpstat", "-d"])
    m = re.search(r":\s*(\S+)\s*$", out.stdout.strip())
    return m.group(1) if m and "no system default" not in out.stdout else None


def print_file(path, printer=None, copies=1, options=None, title=None):
    """Gửi file tới máy in. Trả về mã lệnh in (job id)."""
    args = ["lp"]
    if printer:
        args += ["-d", printer]
    args += ["-n", str(max(1, int(copies)))]
    if title:
        args += ["-t", title[:120]]
    for k, v in (options or {}).items():
        args += ["-o", "%s=%s" % (k, v) if v not in (None, True, "") else k]
    args.append(path)
    out = _run(args)
    if out.returncode != 0:
        raise PrintError("In thất bại: %s" % (out.stderr.strip() or out.stdout.strip()))
    m = re.search(r"request id is (\S+)", out.stdout)
    return m.group(1) if m else out.stdout.strip()


def wait_job(job_id, timeout=180, poll=1.0, stop_event=None):
    """Chờ lệnh in rời khỏi hàng đợi CUPS (đã in xong). True nếu xong, False nếu quá hạn
    (máy in tắt, hết giấy, kẹt giấy... lệnh vẫn nằm trong hàng đợi)."""
    deadline = time.time() + timeout
    while True:
        out = _run(["lpstat", "-o"])
        if not any(l.split()[:1] == [job_id] for l in out.stdout.splitlines()):
            return True
        if time.time() >= deadline or (stop_event is not None and stop_event.is_set()):
            return False
        time.sleep(poll)


def jobs():
    out = _run(["lpstat", "-o"])
    return [l for l in out.stdout.splitlines() if l.strip()]


def cancel(job_id):
    _run(["cancel", job_id])
