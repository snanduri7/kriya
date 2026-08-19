from kriya.workflow.generation_manifest import (
    FileRole,
    build_generation_manifest,
    classify_file_role,
)


def test_manifest_classifies_common_roles_without_stack_specific_names():
    assert classify_file_role("pom.xml") is FileRole.BUILD
    assert classify_file_role("src/main/java/acme/model/Person.java") is FileRole.MODEL
    assert classify_file_role("src/main/java/acme/PersonService.java") is FileRole.SOURCE
    assert classify_file_role("src/main/resources/applicationContext.xml") is FileRole.CONFIG
    assert classify_file_role("src/main/java/acme/Application.java") is FileRole.ENTRYPOINT
    assert classify_file_role("src/test/java/acme/ApplicationTest.java") is FileRole.TEST


def test_manifest_orders_providers_before_consumers_and_records_dependencies():
    paths = [
        "src/test/java/acme/ApplicationTest.java",
        "src/main/java/acme/Application.java",
        "src/main/resources/applicationContext.xml",
        "src/main/java/acme/PersonService.java",
        "src/main/java/acme/model/Person.java",
        "pom.xml",
    ]

    manifest = build_generation_manifest(paths)

    assert manifest.ordered_paths == [
        "pom.xml",
        "src/main/java/acme/model/Person.java",
        "src/main/java/acme/PersonService.java",
        "src/main/resources/applicationContext.xml",
        "src/main/java/acme/Application.java",
        "src/test/java/acme/ApplicationTest.java",
    ]
    app = manifest.entry_for("src/main/java/acme/Application.java")
    assert "pom.xml" in app.depends_on
    assert "src/main/java/acme/model/Person.java" in app.depends_on
    assert "src/main/java/acme/PersonService.java" in app.depends_on
    assert "src/main/resources/applicationContext.xml" in app.depends_on


def test_manifest_prompt_is_explicit_and_deduplicates_paths():
    manifest = build_generation_manifest(["pom.xml", "App.java", "App.java"])
    prompt = manifest.render_prompt()

    assert manifest.ordered_paths == ["pom.xml", "App.java"]
    assert prompt.count("- App.java [") == 1
    assert "role=build" in prompt
    assert "depends_on=pom.xml" in prompt
    assert "imports" in prompt
