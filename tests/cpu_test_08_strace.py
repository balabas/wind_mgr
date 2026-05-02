#!/usr/bin/env python3
"""Test 08: strace the hot main thread to see which syscall it's spinning on.
Run while wind_mgr is hidden and using 100% CPU.

Usage:
  python3 tests/cpu_test_08_strace.py
"""
import subprocess, sys, time, os

def find_pid():
    r = subprocess.run(["pgrep", "-f", "python.*main.py"], capture_output=True, text=True)
    pids = [int(p) for p in r.stdout.strip().split() if p]
    return pids[0] if pids else None

pid = find_pid()
if not pid:
    print("wind_mgr not running", flush=True)
    sys.exit(1)

print(f"Attaching strace to pid={pid} (main thread = hot thread)", flush=True)
print("Sampling for 3 seconds...\n", flush=True)

# Trace only the main thread (tid == pid), capture syscall summary
r = subprocess.run(
    ["strace", "-p", str(pid), "-T", "-e",
     "trace=poll,ppoll,epoll_wait,epoll_pwait,read,recv,recvfrom,recvmsg,select,pselect6",
     "-c",           # summary mode: count calls
     "--trace-path=/proc/self/fd/1"],
    capture_output=True, text=True, timeout=5,
)
print("=== strace summary (syscall counts) ===")
print(r.stderr or r.stdout)

print("\n=== First 20 raw strace lines ===")
r2 = subprocess.run(
    ["strace", "-p", str(pid), "-T", "-e",
     "trace=poll,ppoll,epoll_wait,epoll_pwait,read,recv,recvfrom,recvmsg"],
    capture_output=True, text=True, timeout=2,
)
lines = (r2.stderr or r2.stdout).splitlines()
for line in lines[:20]:
    print(line)
