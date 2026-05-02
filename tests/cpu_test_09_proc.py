#!/usr/bin/env python3
"""Test 09: read /proc/<pid>/task/<tid>/syscall to see what syscall the
hot thread is currently in. No root required.

Usage:
  python3 tests/cpu_test_09_proc.py
"""
import time, subprocess, os, collections

def find_pid():
    r = subprocess.run(["pgrep", "-f", "python.*main.py"], capture_output=True, text=True)
    pids = [int(p) for p in r.stdout.strip().split() if p]
    return pids[0] if pids else None

pid = find_pid()
if not pid:
    print("wind_mgr not running"); exit(1)

# Find the hot thread (tid == pid for main thread)
hot_tid = pid
print(f"Sampling /proc/{pid}/task/{hot_tid}/syscall  for 3 seconds...\n")

syscall_counts = collections.Counter()
wchan_counts = collections.Counter()
samples = 0
t0 = time.monotonic()
while time.monotonic() - t0 < 3.0:
    # Read current syscall
    try:
        with open(f"/proc/{pid}/task/{hot_tid}/syscall") as f:
            line = f.read().strip()
            # format: "<syscall_nr> <args...> <sp> <pc>"
            # or "running" if not in a syscall
            parts = line.split()
            syscall_counts[parts[0]] += 1
    except Exception as e:
        syscall_counts[f"error:{e}"] += 1

    try:
        with open(f"/proc/{pid}/task/{hot_tid}/wchan") as f:
            wchan_counts[f.read().strip()] += 1
    except Exception:
        pass

    samples += 1
    time.sleep(0.001)  # 1ms sampling

# Translate common syscall numbers to names (x86_64)
SYSCALL_NAMES = {
    "0": "read",
    "1": "write",
    "7": "poll",
    "23": "select",
    "202": "epoll_pwait",
    "228": "clock_gettime",
    "230": "clock_nanosleep",
    "232": "epoll_wait",
    "270": "pselect6",
    "281": "epoll_pwait",
    "302": "prlimit64",
    "running": "running (not in syscall)",
}

print(f"=== Syscall distribution ({samples} samples over 3s) ===")
for syscall, count in syscall_counts.most_common():
    name = SYSCALL_NAMES.get(syscall, f"syscall#{syscall}")
    pct = count / samples * 100
    print(f"  {name:30s}  {count:5d}  ({pct:.1f}%)")

print(f"\n=== wchan (kernel wait function) ===")
for wchan, count in wchan_counts.most_common():
    pct = count / samples * 100
    print(f"  {wchan:40s}  {count:5d}  ({pct:.1f}%)")
