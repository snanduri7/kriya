"""S7: the census fixture's seams are themselves pinned (tests/conftest.py ``_containment_census_seam``).

Seam S registers the mount-aware stand-in under the production name "oci" AND makes the canonical image report ABSENT (the normal production result on a machine that never pulled it),
so a semantic test never touches Docker. Seam F2 makes only the image absent. Without these pins a seam could be silently removed and 2,000+ tests would reach for a real Docker
daemon (or a developer's local images) instead of failing here."""
from _containment_standin import MountAwareStandIn

from kriya.tools import containment, containment_oci

CANONICAL_IMAGE = "maven:3.9-eclipse-temurin-17"


def test_the_semantic_seam_registers_the_stand_in_as_oci_and_reports_the_canonical_image_absent():
    assert isinstance(containment._PRODUCTION_BACKEND_REGISTRY["oci"](), MountAwareStandIn)      # pylint: disable=protected-access
    inspection = containment_oci.local_image_inspection(CANONICAL_IMAGE)
    assert inspection.status is containment_oci.ImageInspectStatus.IMAGE_NOT_FOUND and inspection.digest is None


def test_the_fingerprint_seam_reports_the_image_absent_and_leaves_the_production_backend_alone():
    assert containment._PRODUCTION_BACKEND_REGISTRY["oci"] is containment._oci_backend_factory   # pylint: disable=protected-access
    inspection = containment_oci.local_image_inspection(CANONICAL_IMAGE)
    assert inspection.status is containment_oci.ImageInspectStatus.IMAGE_NOT_FOUND and inspection.digest is None
