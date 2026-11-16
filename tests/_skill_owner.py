"""The Skills owner a test writes skill files through (C-04, S4).

Every byte under a skills root goes through one entry on the Skills owner (`SkillEngine.write_skill_path`), which decides by path class and
actor. A test that only SEEDS or EDITS skill files (a provenance record, a staged fact, a promoted rule) is doing what a person does with
the `kriya skills` commands, so it writes as the OPERATOR: `operator_owner` is that engine for the skills root holding the target. A test
about what a RUN may write builds its own run engine (a workspace, no ``operator``) and hands that in instead.
"""
import os
from typing import Any

from kriya.skills.skill import SkillEngine


def operator_owner(target: Any, *, is_root: bool = False) -> SkillEngine:
    """An operator engine for the skills root of ``target``: a skill folder path or a loaded Skill (its root is the parent directory), or
    the skills root itself with ``is_root``."""
    path = getattr(target, "source_path", target)
    root = os.path.abspath(str(path)) if is_root else os.path.dirname(os.path.abspath(str(path)))
    return SkillEngine(root, load_global=False, load_cwd=False, operator=True)
