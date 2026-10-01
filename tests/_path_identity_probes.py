"""Shared filesystem-semantics probes for the ARCH-PLATFORM-001 path-identity
tests. Probing happens only in a test's own tmp directory; production code
never creates a file to decide."""
import os
import unicodedata

NFC_NAME = unicodedata.normalize("NFC", "caf\u00e9.md")
NFD_NAME = unicodedata.normalize("NFD", NFC_NAME)
assert NFC_NAME != NFD_NAME


def aliases(directory, name, variant):
    """Whether this filesystem resolves ``variant`` to the existing ``name``."""
    (directory / name).write_text("probe\n")
    try:
        return os.path.exists(directory / variant) and os.path.samefile(directory / name, directory / variant)
    finally:
        os.unlink(directory / name)


def case_insensitive(tmp_path):
    probe = tmp_path / "probe-case"
    probe.mkdir()
    return aliases(probe, "goal.md", "GOAL.md")


def normalization_insensitive(tmp_path):
    probe = tmp_path / "probe-nfd"
    probe.mkdir()
    return aliases(probe, NFC_NAME, NFD_NAME)
