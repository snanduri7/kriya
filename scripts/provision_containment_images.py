#!/usr/bin/env python3
"""PRD-034 (APT): PROVISION the product-built containment images BEFORE the canonical run.

The registry proxy (``kriya-acq-proxy-base:1``) and the per-toolchain acquisition images (``kriya-acq-tools--<base>:1``) are built by the PRODUCT on first use with ``apt-get`` from the
Debian mirrors (kriya/tools/containment_oci.py ``_ensure_image``). On a cold runner that would put a live apt dependency INSIDE the canonical pytest stage. This script builds them with the
product's own function (nothing is re-implemented) from the digest-pinned base images, so the canonical run finds them present and performs no ``apt-get``:

    provision_containment_images.py build       build (idempotent) the proxy image and one tools image per base in BASES        (PROVISION - contacts Debian mirrors)
    provision_containment_images.py record OUT  write {tag: image id, parent base image id} for every ``kriya-acq-*`` image        (no network)
    provision_containment_images.py check OUT   fail if the images differ from the record in OUT - i.e. an image was (re)built or added since (used after the canonical run)

Image-build reproducibility from a clean Debian snapshot is NOT a signed PRD-034 requirement (the signed `images` stage pins the BASE images by digest); the certification consumes the
images built here and records their ids. If clean-rebuild reproducibility is ever required, an immutable Debian snapshot repository is the route, not this script.
"""
import json
import subprocess
import sys

sys.path.insert(0, ".")

BASES = (
    "maven:3.9-eclipse-temurin-8", "maven:3.9-eclipse-temurin-17", "maven:3.9-eclipse-temurin-21",
    "gradle:8-jdk8", "gradle:8-jdk17", "python:3.12-slim", "debian:bookworm-slim",
)


def _image_ids():
    out = subprocess.run(["docker", "images", "--no-trunc", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"], capture_output=True, text=True, check=True).stdout
    return {line.split()[0]: line.split()[1] for line in out.splitlines() if line.startswith("kriya-acq-")}


def build() -> int:
    from kriya.tools import containment_oci as oci  # pylint: disable=import-outside-toplevel

    for base in BASES:
        oci._ensure_image("docker", oci._acquisition_image_tag(base), oci._ACQUISITION_TOOLS_DOCKERFILE.format(base_image=base), purpose="acquisition firewall-tooling")  # pylint: disable=protected-access
    oci._ensure_image("docker", oci._PROXY_IMAGE_TAG, oci._PROXY_DOCKERFILE, purpose="registry proxy")  # pylint: disable=protected-access
    print(f"[images] provisioned {len(BASES) + 1} product-built images")
    return 0


def main(argv) -> int:
    if len(argv) >= 2 and argv[1] == "build":
        return build()
    if len(argv) == 3 and argv[1] == "record":
        ids = _image_ids()
        with open(argv[2], "w", encoding="utf-8") as handle:
            json.dump({"images": ids}, handle, indent=2, sort_keys=True)
        print(f"[images] recorded {len(ids)} kriya-acq-* images")
        return 0 if ids else 1
    if len(argv) == 3 and argv[1] == "check":
        before = json.load(open(argv[2], encoding="utf-8"))["images"]
        after = _image_ids()
        problems = [f"BUILT/ADDED during the run (a live apt build inside the canonical run): {tag}" for tag in sorted(set(after) - set(before))]
        problems += [f"REBUILT during the run: {tag}" for tag in sorted(set(before) & set(after)) if before[tag] != after[tag]]
        problems += [f"disappeared: {tag}" for tag in sorted(set(before) - set(after))]
        print("[images] check: " + ("PASS" if not problems else "FAIL") + "".join(f"\n  - {p}" for p in problems))
        return 0 if not problems else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
