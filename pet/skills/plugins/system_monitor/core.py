import psutil
import logging
import platform
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _format_bytes(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}PB"


def get_overview() -> dict:
    try:
        cpu_percent = psutil.cpu_percent(interval=0.3)
        cpu_count = psutil.cpu_count()

        mem = psutil.virtual_memory()
        # 跨平台磁盘：Windows 用 C:，其他用 /
        disk_path = "C:\\" if platform.system() == "Windows" else "/"
        disk = psutil.disk_usage(disk_path)

        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        uptime_str = str(timedelta(seconds=int(uptime.total_seconds())))

        result = {
            "cpu_percent": cpu_percent,
            "cpu_cores": cpu_count,
            "memory_percent": mem.percent,
            "memory_used": _format_bytes(mem.used),
            "memory_total": _format_bytes(mem.total),
            "disk_percent": disk.percent,
            "disk_used": _format_bytes(disk.used),
            "disk_total": _format_bytes(disk.total),
            "uptime": uptime_str,
            "os": f"{platform.system()} {platform.release()}",
        }

        battery = psutil.sensors_battery()
        if battery:
            result["battery_percent"] = battery.percent
            result["battery_charging"] = battery.power_plugged

        parts = [
            f"CPU {cpu_percent}% ({cpu_count}核)",
            f"内存 {mem.percent}% ({_format_bytes(mem.used)}/{_format_bytes(mem.total)})",
            f"磁盘 {disk.percent}%",
        ]
        if battery:
            status = "充电中" if battery.power_plugged else "放电中"
            parts.append(f"电池 {battery.percent}% ({status})")
        parts.append(f"已运行 {uptime_str}")

        result["summary"] = " | ".join(parts)
        return result
    except Exception as e:
        return {"error": str(e)}


def get_top_processes(count: int = 5) -> dict:
    count = max(1, min(count, 20))
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            if info["cpu_percent"] is not None:
                procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x["cpu_percent"] or 0, reverse=True)
    top = procs[:count]

    lines = [f"CPU 占用最高的 {len(top)} 个进程："]
    for i, p in enumerate(top, 1):
        lines.append(
            f"  {i}. {p['name']} (PID {p['pid']}) "
            f"- CPU {p['cpu_percent']:.1f}% / 内存 {p['memory_percent']:.1f}%"
        )

    return {
        "processes": top,
        "summary": "\n".join(lines),
    }


def get_memory_detail() -> dict:
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    result = {
        "ram_total": _format_bytes(mem.total),
        "ram_used": _format_bytes(mem.used),
        "ram_available": _format_bytes(mem.available),
        "ram_percent": mem.percent,
        "swap_total": _format_bytes(swap.total),
        "swap_used": _format_bytes(swap.used),
        "swap_percent": swap.percent,
    }

    result["summary"] = (
        f"物理内存: {mem.percent}% ({_format_bytes(mem.used)}/{_format_bytes(mem.total)}, "
        f"可用 {_format_bytes(mem.available)}) | "
        f"交换分区: {swap.percent}% ({_format_bytes(swap.used)}/{_format_bytes(swap.total)})"
    )
    return result


def get_network() -> dict:
    counters = psutil.net_io_counters()
    result = {
        "bytes_sent": _format_bytes(counters.bytes_sent),
        "bytes_recv": _format_bytes(counters.bytes_recv),
        "packets_sent": counters.packets_sent,
        "packets_recv": counters.packets_recv,
    }
    result["summary"] = (
        f"网络: 已发送 {_format_bytes(counters.bytes_sent)} / "
        f"已接收 {_format_bytes(counters.bytes_recv)}"
    )
    return result