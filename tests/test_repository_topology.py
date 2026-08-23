import json
import os

from kriya.workflow.repository_topology import detect_repository_topology


def _write(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def test_single_maven_app(tmp_path):
    d = str(tmp_path)
    _write(os.path.join(d, "pom.xml"), "<project><groupId>x</groupId></project>")
    _write(
        os.path.join(d, "src/main/java/com/example/Application.java"),
        "package com.example;\npublic class Application {\n  public static void main(String[] args) {}\n}\n",
    )
    t = detect_repository_topology(d)
    assert t.build_system == "maven"
    assert t.build_roots == (".",)
    assert t.modules == ()
    assert t.is_multi_module is False
    assert t.entrypoints == ("com.example.Application",)


def test_multi_module_maven_declared(tmp_path):
    d = str(tmp_path)
    _write(
        os.path.join(d, "pom.xml"),
        "<project><modules><module>protocol</module><module>server</module></modules></project>",
    )
    _write(os.path.join(d, "protocol/pom.xml"), "<project/>")
    _write(os.path.join(d, "server/pom.xml"), "<project/>")
    t = detect_repository_topology(d)
    assert t.build_system == "maven"
    assert set(t.modules) == {"protocol", "server"}
    assert set(t.build_roots) == {".", "protocol", "server"}
    assert t.is_multi_module is True


def test_multi_module_maven_without_aggregator(tmp_path):
    d = str(tmp_path)
    _write(os.path.join(d, "protocol/pom.xml"), "<project/>")
    _write(os.path.join(d, "server/pom.xml"), "<project/>")
    t = detect_repository_topology(d)
    assert t.build_system is None
    assert set(t.modules) == {"protocol", "server"}
    assert "." not in t.build_roots
    assert t.is_multi_module is True


def test_gradle_multi_module(tmp_path):
    d = str(tmp_path)
    _write(os.path.join(d, "build.gradle"), "")
    _write(os.path.join(d, "settings.gradle"), "include ':app', ':lib'\n")
    _write(os.path.join(d, "app/build.gradle"), "")
    _write(os.path.join(d, "lib/build.gradle"), "")
    t = detect_repository_topology(d)
    assert t.build_system == "gradle"
    assert set(t.modules) == {"app", "lib"}


def test_npm_workspaces(tmp_path):
    d = str(tmp_path)
    _write(os.path.join(d, "package.json"), json.dumps({"name": "root", "workspaces": ["packages/a", "packages/b"]}))
    t = detect_repository_topology(d)
    assert t.build_system == "npm"
    assert set(t.modules) == {"packages/a", "packages/b"}


def test_python_single_project(tmp_path):
    d = str(tmp_path)
    _write(os.path.join(d, "pyproject.toml"), '[project]\nname="x"\n')
    _write(os.path.join(d, "main.py"), 'if __name__ == "__main__":\n    pass\n')
    t = detect_repository_topology(d)
    assert t.build_system == "python"
    assert t.is_multi_module is False
    assert "main.py" in t.entrypoints


def test_empty_workspace(tmp_path):
    t = detect_repository_topology(str(tmp_path))
    assert t.build_system is None
    assert t.build_roots == ()
    assert t.is_multi_module is False


def test_to_dict_is_json_serializable(tmp_path):
    json.dumps(detect_repository_topology(str(tmp_path)).to_dict())
