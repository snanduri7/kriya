"""Pull and verify the digest-pinned images certification requires.

    python scripts/pinned_images.py --pull     # CI preflight: pull each by digest, then verify
    python scripts/pinned_images.py --verify   # scripts/certify.sh: verify only, never pull

An image counts as present only when the local image's RepoDigests contain
exactly the pinned reference. Exit 0 when every image is present, 1 with the
missing ones named otherwise, 2 when docker itself is unavailable.
"""
import json
import os
import shutil
import subprocess
import sys
from typing import List, Optional

MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "certification", "pinned_images.txt")


def pinned_images(path: str = MANIFEST) -> List[str]:
    with open(path, "r", encoding="utf-8") as handle:
        images = [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")]
    for image in images:
        repository, sep, digest = image.partition("@sha256:")
        if not (repository and sep and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)):
            raise ValueError(f"not pinned by digest: {image!r}")
    return images


def present(docker: str, image: str) -> bool:
    inspect = subprocess.run([docker, "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
                             capture_output=True, text=True, timeout=60)
    if inspect.returncode != 0:
        return False
    try:
        return image in json.loads(inspect.stdout.strip() or "[]")
    except ValueError:
        return False


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
            subprocess.run([docker, "pull", image], check=False, timeout=1800)
        if not present(docker, image):
            missing.append(image)
    for image in missing:
        print(f"[pinned-images] MISSING: {image}", file=sys.stderr)
    print(f"[pinned-images] {len(pinned_images(manifest)) - len(missing)} present, {len(missing)} missing")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
