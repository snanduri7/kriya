from kriya.workflow.dependency_invalidation import (
    dependent_closure,
    invalidate_validated_revisions,
)


def _dependencies():
    return {
        "pom.xml": [],
        "Person.java": ["pom.xml"],
        "PersonService.java": ["pom.xml", "Person.java"],
        "applicationContext.xml": ["pom.xml", "Person.java", "PersonService.java"],
        "Application.java": [
            "pom.xml", "Person.java", "PersonService.java", "applicationContext.xml",
        ],
        "ApplicationTest.java": [
            "pom.xml", "Person.java", "PersonService.java", "applicationContext.xml",
            "Application.java",
        ],
    }


def test_dependent_closure_preserves_unrelated_providers_and_adds_consumers():
    assert dependent_closure(["PersonService.java"], _dependencies()) == [
        "PersonService.java",
        "applicationContext.xml",
        "Application.java",
        "ApplicationTest.java",
    ]


def test_dependency_invalidation_removes_only_changed_and_dependent_revisions():
    validated = {path: f"revision-{path}" for path in _dependencies()}

    removed = invalidate_validated_revisions(
        validated, ["PersonService.java"], _dependencies(),
    )

    assert removed == [
        "PersonService.java",
        "applicationContext.xml",
        "Application.java",
        "ApplicationTest.java",
    ]
    assert set(validated) == {"pom.xml", "Person.java"}


def test_unknown_changed_file_is_invalidated_without_expanding_manifest():
    validated = {"pom.xml": "rev", "notes.txt": "old"}

    removed = invalidate_validated_revisions(
        validated, ["notes.txt"], {"pom.xml": []},
    )

    assert removed == ["notes.txt"]
    assert validated == {"pom.xml": "rev"}
