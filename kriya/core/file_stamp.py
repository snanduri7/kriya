"""Stat-based cache validation that is safe against coarse file timestamps
(FILE-STAMP-RACY-CACHE-001).

A cache that skips re-reading a file while its stat is unchanged is only
correct if every change moves the stat. It does not: Linux stamps files
from a coarse kernel clock (a few milliseconds per tick), so two writes
within one tick keep the same mtime, and a same-size rewrite then looks
unchanged (APFS's finer clock hid this on macOS). The rule is git's
"racy-clean" one: a stat is trusted only when the file had already settled
- its mtime older than RACY_WINDOW_NS - at the moment it was cached. Any
later write then lands on a later tick, so it always changes the mtime.
A file still inside the window is simply re-read.
"""
import os
import time
from dataclasses import dataclass
from typing import Optional

# At least the coarsest mtime granularity Kriya can meet (2 s on FAT/exFAT;
# ext4, XFS, APFS and overlayfs are far finer).
RACY_WINDOW_NS = 2_000_000_000


@dataclass(frozen=True)
class FileStamp:
    mtime_ns: int
    size: int
    inode: int
    device: int
    # The file's mtime was older than the racy window when this was taken.
    settled: bool


def file_stamp(stat: os.stat_result, now_ns: Optional[int] = None) -> FileStamp:
    now = time.time_ns() if now_ns is None else now_ns
    return FileStamp(stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_dev,
                     settled=stat.st_mtime_ns < now - RACY_WINDOW_NS)


def unchanged_since(cached: Optional[FileStamp], stat: os.stat_result) -> bool:
    """True only when ``cached`` proves the file still holds what was read:
    it was settled when cached and the stat is identical."""
    return cached is not None and cached.settled and (
        cached.mtime_ns, cached.size, cached.inode, cached.device
    ) == (stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_dev)
