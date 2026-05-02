#!/usr/bin/env python3
"""Test 10: Real-time CPU sampling with per-process breakdown.
Run this, then hide/show the wind_mgr window to see CPU change.

Usage:
  python3 tests/cpu_test_10_realtime.py
"""
import time, subprocess, os, sys

def find_pid():
    r = subprocess.run(["pgrep", "-f", "python.*main.py"], capture_output=True, text=True)
    pids = [int(p) for p in r.stdout.strip().split() if p]
    return pids[0] if pids else None

try:
    import psutil
except ImportError:
    print("pip install psutil"); sys.exit(1)

pid = find_pid()
if not pid:
    print("wind_mgr not running"); sys.exit(1)

proc = psutil.Process(pid)
print(f"Monitoring wind_mgr pid={pid}. Hide/show the window. Ctrl+C to stop.\n")
print(f"{'Time':>6}  {'main%':>6}  {'children':>60}  wchan")
print("-" * 100)

# prime the cpu_percent counters
proc.cpu_percent(interval=None)
children_prev = {}
for c in proc.children(recursive=True):
    try:
        c.cpu_percent(interval=None)
        children_prev[c.pid] = c
    except Exception:
        pass

time.sleep(1.0)

t0 = time.monotonic()
try:
    while True:
        elapsed = time.monotonic() - t0

        try:
            main_cpu = proc.cpu_percent(interval=None)
        except psutil.NoSuchProcess:
            print("wind_mgr exited"); break

        child_parts = []
        try:
            for c in proc.children(recursive=True):
                try:
                    cpu = c.cpu_percent(interval=None)
                    if cpu > 0.5:
                        name = c.name()[:16]
                        child_parts.append(f"{name}={cpu:.0f}%")
                    children_prev[c.pid] = c
                except Exception:
                    pass
        except Exception:
            pass

        try:
            with open(f"/proc/{pid}/task/{pid}/wchan") as f:
                wchan = f.read().strip() or "0"
        except Exception:
            wchan = "?"

        children_str = "  ".join(child_parts) if child_parts else "-"
        print(f"{elapsed:6.1f}s  {main_cpu:5.1f}%  {children_str:<60}  {wchan}")
        time.sleep(1.0)
except KeyboardInterrupt:
    print("\nDone.")
