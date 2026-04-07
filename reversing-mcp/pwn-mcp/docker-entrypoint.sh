#!/bin/sh
# Register QEMU binfmt handlers if binfmt_misc is available.
# Requires SYS_ADMIN or pre-configured host binfmt; fails silently otherwise.
if [ -f /proc/sys/fs/binfmt_misc/register ]; then
    update-binfmts --enable 2>/dev/null || true
fi

exec "$@"
