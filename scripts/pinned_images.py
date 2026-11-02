"""Pull and verify the digest-pinned images certification requires.

    python scripts/pinned_images.py --pull     # CI preflight: pull each by digest, then verify
    python scripts/pinned_images.py --verify   # scripts/certify.sh: verify only, never pull

An image counts as present only when the local image's RepoDigests contain
exactly the pinned reference. A manifest line may also carry the TAG the tests
and the product refer to (``repo:tag@sha256:...``): it is then also required that
``repo:tag`` resolves to that very image (``--pull`` applies the tag after pulling
by digest; ``--verify`` never changes anything), so a re-pointed tag fails
certification instead of silently running different bytes. Exit 0 when every
image is present, 1 with the missing ones named otherwise, 2 when docker itself
is unavailable.
"""
import json
import os
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "certification", "pinned_images.txt")


def split(line: str) -> Tuple[str, Optional[str]]:
    """(``repo@sha256:...`` digest reference, tag or None) of a manifest line (``repo[:tag]@sha256:...``)."""
    repository, sep, digest = line.partition("@sha256:")
    name, colon, tag = repository.rpartition(":")
    if colon and "/" not in tag:
        repository_name, tagged = name, tag
    else:
        repository_name, tagged = repository, None
    if not (repository_name and sep):
        raise ValueError(f"not pinned by digest: {line!r}")
    return f"{repository_name}@sha256:{digest}", tagged


def pinned_images(path: str = MANIFEST) -> List[str]:
    with open(path, "r", encoding="utf-8") as handle:
        images = [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")]
    for image in images:
        _repository, sep, digest = image.partition("@sha256:")
        if not (_repository and sep and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)):
            raise ValueError(f"not pinned by digest: {image!r}")
        split(image)
    return images


def _inspect(docker: str, reference: str, template: str) -> Optional[str]:
    result = subprocess.run([docker, "image", "inspect", reference, "--format", template], capture_output=True, text=True, timeout=60)
    return result.stdout.strip() if result.returncode == 0 else None


def present(docker: str, line: str) -> bool:
    digest_reference, tag = split(line)
    digests = _inspect(docker, digest_reference, "{{json .RepoDigests}}")
    if digests is None:
        return False
    try:
        if digest_reference not in json.loads(digests or "[]"):
            return False
    except ValueError:
        return False
    if tag is None:
        return True
    repository = digest_reference.partition("@")[0]
    pinned_id, tagged_id = _inspect(docker, digest_reference, "{{.Id}}"), _inspect(docker, f"{repository}:{tag}", "{{.Id}}")
    return bool(pinned_id) and pinned_id == tagged_id


def main(argv: Optional[List[str]] = None, manifest: str = MANIFEST) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in (["--pull"], ["--verify"]):
        print(__doc__, file=sys.stderr)
        return 2
    docker = shutil.which("docker")
    if docker is None:
        print("[pinned-images] UNAVAILABLE: docker CLI not on PATH", file=sys.stderr)
        return 2
    missing = []
    for image in pinned_images(manifest):
        if args == ["--pull"] and not present(docker, image):
            digest_reference, tag = split(image)
            if subprocess.run([docker, "pull", digest_reference], check=False, timeout=1800).returncode == 0 and tag is not None:
                subprocess.run([docker, "tag", digest_reference, f"{digest_reference.partition('@')[0]}:{tag}"], check=False, timeout=60)
        if not present(docker, image):
            missing.append(image)
    for image in missing:
        print(f"[pinned-images] MISSING: {image}", file=sys.stderr)
    print(f"[pinned-images] {len(pinned_images(manifest)) - len(missing)} present, {len(missing)} missing")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
