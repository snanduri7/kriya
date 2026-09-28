"""FILE-STAMP-RACY-CACHE-001: a stat-validated read cache must never return
stale content. Linux stamps files from a coarse kernel clock, so a same-size
rewrite within one tick keeps the mtime (Linux reproduction of the hosted
runner: test_resolver_revision_changes_with_content and
test_assessment_states failed). A stat is trusted only for a file that had
settled - mtime older than RACY_WINDOW_NS - when it was cached.

The coarse clock is reproduced deterministically on any filesystem by
restoring the original mtime after a same-size rewrite.
"""
import json
import os
import time

from kriya.core import model_qualification as mq
from kriya.core.file_stamp import RACY_WINDOW_NS, file_stamp, unchanged_since
from kriya.workflow.context_source import CurrentSourceResolver, SourceDerivationCache


def _rewrite_keeping_stat(path, content):
    before = os.stat(path)
    path.write_text(content)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.stat(path)
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)


def _settle(path):
    old = time.time_ns() - 10 * RACY_WINDOW_NS
    os.utime(path, ns=(old, old))


def test_a_same_tick_same_size_rewrite_is_never_served_stale(tmp_path):
    workspace, worktree = tmp_path / "workspace", tmp_path / "worktree"
    workspace.mkdir()
    worktree.mkdir()
    for root in (workspace, worktree):
        (root / "Target.java").write_text("VERSION_A")
    resolver = CurrentSourceResolver(str(workspace), str(worktree))
    first = resolver.resolve("Target.java")
    _rewrite_keeping_stat(worktree / "Target.java", "VERSION_B")
    second = resolver.resolve("Target.java")
    assert second.revision != first.revision and second.content == "VERSION_B"


def test_a_settled_unchanged_file_is_still_reused(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    _settle(tmp_path / "a.py")
    cache = SourceDerivationCache()
    assert cache.read(str(tmp_path), "a.py")[0] == "x = 1\n"
    assert cache.read(str(tmp_path), "a.py")[0] == "x = 1\n"
    assert cache.content_read_hits == 1


def test_a_settled_file_that_changes_is_read_again(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("x = 1\n")
    _settle(path)
    cache = SourceDerivationCache()
    cache.read(str(tmp_path), "a.py")
    path.write_text("x = 2\n")  # a write after caching lands on a later tick
    assert cache.read(str(tmp_path), "a.py")[0] == "x = 2\n"


def test_a_qualification_record_rewritten_in_the_same_tick_is_reparsed(tmp_path):
    path = tmp_path / "record.json"
    path.write_text(json.dumps({"case": "pass"}))
    assert mq._cached_record_file(str(path)) == {"case": "pass"}
    _rewrite_keeping_stat(path, json.dumps({"case": "fail"}))
    assert mq._cached_record_file(str(path)) == {"case": "fail"}


def test_the_stamp_rule():
    stat = os.stat(__file__)
    fresh = file_stamp(stat, now_ns=stat.st_mtime_ns)
    settled = file_stamp(stat, now_ns=stat.st_mtime_ns + RACY_WINDOW_NS + 1)
    assert not unchanged_since(fresh, stat) and unchanged_since(settled, stat)
    assert not unchanged_since(None, stat)


def test_a_settled_file_replaced_with_its_mtime_preserved_is_read_again(tmp_path):
    """rsync -t, tar -x and cp -p keep the source's mtime; a different size
    (or inode) still proves the content changed."""
    path = tmp_path / "a.py"
    path.write_text("x = 1\n")
    _settle(path)
    cache = SourceDerivationCache()
    cache.read(str(tmp_path), "a.py")
    kept_mtime = os.stat(path).st_mtime_ns
    path.write_text("x = 1000\n")
    os.utime(path, ns=(kept_mtime, kept_mtime))
    assert cache.read(str(tmp_path), "a.py")[0] == "x = 1000\n"
