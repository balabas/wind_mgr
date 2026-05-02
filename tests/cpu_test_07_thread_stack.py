#!/usr/bin/env python3
"""Test 07: attach to running wind_mgr and dump all thread stacks.
Run while wind_mgr is hidden and using 100% CPU.

Usage:
  python3 tests/cpu_test_07_thread_stack.py <PID>
  # or without PID to find it automatically
"""
import sys, os, traceback, signal, time

def find_windmgr_pid():
    import subprocess
    result = subprocess.run(
        ["pgrep", "-f", "python.*main.py"],
        capture_output=True, text=True
    )
    pids = [int(p) for p in result.stdout.strip().split() if p]
    if not pids:
        result = subprocess.run(["pgrep", "-f", "wind_mgr"], capture_output=True, text=True)
        pids = [int(p) for p in result.stdout.strip().split() if p]
    return pids

if len(sys.argv) > 1:
    pid = int(sys.argv[1])
else:
    pids = find_windmgr_pid()
    if not pids:
        print("Could not find wind_mgr process. Pass PID as argument.", flush=True)
        sys.exit(1)
    pid = pids[0]
    print(f"Found wind_mgr at pid={pid}", flush=True)

# Try py-spy first (best option)
import subprocess
try:
    r = subprocess.run(["py-spy", "top", "--pid", str(pid), "--duration", "5"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        print(r.stdout)
        sys.exit(0)
    else:
        print("py-spy failed:", r.stderr[:200], flush=True)
except FileNotFoundError:
    print("py-spy not found, trying SIGUSR1 traceback dump...", flush=True)
except subprocess.TimeoutExpired:
    pass

# Fallback: send SIGUSR2 to dump threads via faulthandler (if enabled)
# Or just use /proc to read thread info
try:
    import psutil, threading
    p = psutil.Process(pid)
    print(f"\n=== Process {pid} threads ===", flush=True)
    for t in p.threads():
        cpu_s = t.user_time + t.system_time
        print(f"  tid={t.id}  user+sys={cpu_s:.3f}s", flush=True)

    print(f"\n=== psutil per-thread CPU (sampled 3s) ===", flush=True)
    snapshot1 = {t.id: (t.user_time, t.system_time) for t in p.threads()}
    time.sleep(3)
    snapshot2 = {t.id: (t.user_time, t.system_time) for t in p.threads()}
    for tid, (u2, s2) in snapshot2.items():
        u1, s1 = snapshot1.get(tid, (u2, s2))
        delta = (u2 - u1) + (s2 - s1)
        pct = delta / 3.0 * 100
        print(f"  tid={tid}  delta={delta:.3f}s  ~{pct:.1f}%cpu", flush=True)
except Exception as e:
    print(f"Error: {e}", flush=True)
