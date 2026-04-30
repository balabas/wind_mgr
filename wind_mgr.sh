#!/bin/bash
# wind_mgr launcher — strips VS Code snap library overrides before starting.
# Run this from any terminal: bash ~/Documents/projects/wind_mgr/wind_mgr.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Unset VS Code snap GTK/GDK overrides that conflict with system libraries
unset GDK_PIXBUF_MODULE_FILE
unset GTK_EXE_PREFIX
unset GSETTINGS_SCHEMA_DIR
unset SNAP
unset SNAP_ARCH
unset SNAP_CONTEXT
unset SNAP_EUID
unset SNAP_INSTANCE_NAME
unset SNAP_LAUNCHER_ARCH_TRIPLET
unset SNAP_REAL_HOME
unset SNAP_REVISION
unset SNAP_UID
unset SNAP_USER_COMMON
unset SNAP_USER_DATA

export DISPLAY="${DISPLAY:-:0}"
cd "$SCRIPT_DIR"

LOG_FILE="/tmp/windmgr.log"
echo "Log: $LOG_FILE"
exec /usr/bin/python3 main.py "$@" 2>&1 | tee "$LOG_FILE"
