import abc
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

COMMON_ALIASES = {
    "ignite": ("org.apache.ignite", "ignite-core"),
    "ignite-core": ("org.apache.ignite", "ignite-core"),
    "ignite-spring": ("org.apache.ignite", "ignite-spring"),
    "artemis": ("org.apache.activemq", "artemis-server"),
    "artemis-server": ("org.apache.activemq", "artemis-server"),
    "qpid": ("org.apache.qpid", "qpid-broker-core"),
    "qpid-jms": ("org.apache.qpid", "qpid-jms-client"),
    "qpid-jms-client": ("org.apache.qpid", "qpid-jms-client"),
    "spring-boot": ("org.springframework.boot", "spring-boot-starter"),
    "spring-context": ("org.springframework", "spring-context")
}

def parse_iso_datetime(date_str: str) -> Optional[datetime]:
    """Helper to safely parse ISO-8601 datetime strings."""
    if not date_str:
        return None
    try:
        cleaned = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except Exception as e:
        logger.debug(f"Failed to parse datetime '{date_str}': {e}")
        return None

class KnowledgeCache:
    """SQLite-based cache for package release dates."""
    def __init__(self, memory_dir: str) -> None:
        self.db_path = os.path.join(os.path.abspath(memory_dir), "knowledge_cache.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS release_cache (
                        ecosystem TEXT,
                        package TEXT,
                        version TEXT,
                        release_date TEXT,
                        PRIMARY KEY (ecosystem, package, version)
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to initialize KnowledgeCache DB: {e}")

    def get_release_date(self, ecosystem: str, package: str, version: str) -> Optional[datetime]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT release_date FROM release_cache WHERE ecosystem = ? AND package = ? AND version = ?",
                    (ecosystem.lower(), package.lower(), version.lower())
                )
                row = cursor.fetchone()
                if row and row[0]:
                    return parse_iso_datetime(row[0])
        except Exception as e:
            logger.debug(f"Failed to read from KnowledgeCache: {e}")
        return None

    def set_release_date(self, ecosystem: str, package: str, version: str, release_date: datetime) -> None:
        try:
            date_str = release_date.isoformat()
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO release_cache (ecosystem, package, version, release_date) VALUES (?, ?, ?, ?)",
                    (ecosystem.lower(), package.lower(), version.lower(), date_str)
                )
                conn.commit()
        except Exception as e:
            logger.debug(f"Failed to write to KnowledgeCache: {e}")

class RegistryAdapter(abc.ABC):
    """Abstract base class for ecosystem registries."""
    @abc.abstractmethod
    def get_release_date(self, package: str, version: str, offline: bool = False, cache: Optional[KnowledgeCache] = None) -> Optional[datetime]:
        pass

class MavenCentralAdapter(RegistryAdapter):
    """Adapter for Maven Central (Java ecosystem)."""
    def get_release_date(self, package: str, version: str, offline: bool = False, cache: Optional[KnowledgeCache] = None) -> Optional[datetime]:
        if cache:
            cached = cache.get_release_date("java", package, version)
            if cached:
                return cached
        if offline:
            return None
        
        # Resolve group and artifact
        if ":" in package:
            group, artifact = package.split(":", 1)
        else:
            group, artifact = COMMON_ALIASES.get(package.lower(), (package, package))
            
        url = f"https://search.maven.org/solrsearch/select?q=g:%22{group}%22+AND+a:%22{artifact}%22+AND+v:%22{version}%22&wt=json"
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    docs = resp.json().get("response", {}).get("docs", [])
                    if docs:
                        ts = docs[0].get("timestamp")
                        if ts:
                            dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
                            if cache:
                                cache.set_release_date("java", package, version, dt)
                            return dt
        except Exception as e:
            logger.warning(f"MavenCentral query failed for {package}:{version} - {e}")
        return None

class PyPIAdapter(RegistryAdapter):
    """Adapter for PyPI (Python ecosystem)."""
    def get_release_date(self, package: str, version: str, offline: bool = False, cache: Optional[KnowledgeCache] = None) -> Optional[datetime]:
        if cache:
            cached = cache.get_release_date("python", package, version)
            if cached:
                return cached
        if offline:
            return None
        url = f"https://pypi.org/pypi/{package}/{version}/json"
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    urls = resp.json().get("urls", [])
                    if urls and "upload_time" in urls[0]:
                        dt = parse_iso_datetime(urls[0]["upload_time"])
                        if dt and cache:
                            cache.set_release_date("python", package, version, dt)
                        return dt
        except Exception as e:
            logger.warning(f"PyPI query failed for {package}=={version} - {e}")
        return None

class NpmAdapter(RegistryAdapter):
    """Adapter for npm registry (Node/JavaScript ecosystem)."""
    def get_release_date(self, package: str, version: str, offline: bool = False, cache: Optional[KnowledgeCache] = None) -> Optional[datetime]:
        if cache:
            cached = cache.get_release_date("javascript", package, version)
            if cached:
                return cached
        if offline:
            return None
        url = f"https://registry.npmjs.org/{package}"
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    times = resp.json().get("time", {})
                    if version in times:
                        dt = parse_iso_datetime(times[version])
                        if dt and cache:
                            cache.set_release_date("javascript", package, version, dt)
                        return dt
        except Exception as e:
            logger.warning(f"npm query failed for {package}@{version} - {e}")
        return None

class RubyGemsAdapter(RegistryAdapter):
    """Adapter for RubyGems (Ruby ecosystem)."""
    def get_release_date(self, package: str, version: str, offline: bool = False, cache: Optional[KnowledgeCache] = None) -> Optional[datetime]:
        if cache:
            cached = cache.get_release_date("ruby", package, version)
            if cached:
                return cached
        if offline:
            return None
        url = f"https://rubygems.org/api/v1/versions/{package}.json"
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    versions_list = resp.json()
                    for v in versions_list:
                        if v.get("number") == version:
                            created_at = v.get("created_at")
                            if created_at:
                                dt = parse_iso_datetime(created_at)
                                if dt and cache:
                                    cache.set_release_date("ruby", package, version, dt)
                                return dt
        except Exception as e:
            logger.warning(f"RubyGems query failed for {package}:{version} - {e}")
        return None

class GoModulesAdapter(RegistryAdapter):
    """Adapter for Go Modules proxy."""
    def get_release_date(self, package: str, version: str, offline: bool = False, cache: Optional[KnowledgeCache] = None) -> Optional[datetime]:
        if cache:
            cached = cache.get_release_date("go", package, version)
            if cached:
                return cached
        if offline:
            return None
        escaped_pkg = package.replace(":", "/")
        url = f"https://proxy.golang.org/{escaped_pkg}/@v/v{version.lstrip('v')}.info"
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    time_str = resp.json().get("Time")
                    if time_str:
                        dt = parse_iso_datetime(time_str)
                        if dt and cache:
                            cache.set_release_date("go", package, version, dt)
                        return dt
        except Exception as e:
            logger.warning(f"Go proxy query failed for {package}@{version} - {e}")
        return None

class CargoAdapter(RegistryAdapter):
    """Adapter for Cargo (Rust ecosystem)."""
    def get_release_date(self, package: str, version: str, offline: bool = False, cache: Optional[KnowledgeCache] = None) -> Optional[datetime]:
        if cache:
            cached = cache.get_release_date("rust", package, version)
            if cached:
                return cached
        if offline:
            return None
        url = f"https://crates.io/api/v1/crates/{package}/{version}"
        headers = {"User-Agent": "Kriya-Agent/1.0 (kriya-ai@example.com)"}
        try:
            with httpx.Client(timeout=8.0, headers=headers) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    created_at = resp.json().get("version", {}).get("created_at")
                    if created_at:
                        dt = parse_iso_datetime(created_at)
                        if dt and cache:
                            cache.set_release_date("rust", package, version, dt)
                        return dt
        except Exception as e:
            logger.warning(f"crates.io query failed for {package}:{version} - {e}")
        return None

class NuGetAdapter(RegistryAdapter):
    """Adapter for NuGet (.NET ecosystem)."""
    def get_release_date(self, package: str, version: str, offline: bool = False, cache: Optional[KnowledgeCache] = None) -> Optional[datetime]:
        if cache:
            cached = cache.get_release_date("dotnet", package, version)
            if cached:
                return cached
        if offline:
            return None
        lower_pkg = package.lower()
        lower_ver = version.lower()
        url = f"https://api.nuget.org/v3/registration5/{lower_pkg}/{lower_ver}.json"
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    published = resp.json().get("published")
                    if published:
                        dt = parse_iso_datetime(published)
                        if dt and cache:
                            cache.set_release_date("dotnet", package, version, dt)
                        return dt
        except Exception as e:
            logger.warning(f"NuGet query failed for {package}:{version} - {e}")
        return None

class NullAdapter(RegistryAdapter):
    """Fallback no-op adapter."""
    def get_release_date(self, package: str, version: str, offline: bool = False, cache: Optional[KnowledgeCache] = None) -> Optional[datetime]:
        return None

class RegistryAdapterFactory:
    @staticmethod
    def from_stack(stack: str) -> RegistryAdapter:
        if stack == "java":
            return MavenCentralAdapter()
        elif stack == "python":
            return PyPIAdapter()
        elif stack == "javascript" or stack == "node":
            return NpmAdapter()
        elif stack == "ruby":
            return RubyGemsAdapter()
        elif stack == "go":
            return GoModulesAdapter()
        elif stack == "rust":
            return CargoAdapter()
        elif stack == "dotnet":
            return NuGetAdapter()
        return NullAdapter()

    @staticmethod
    def detect_stack(workspace_path: str) -> str:
        """Helper to determine language stack from workspace directory files."""
        if not workspace_path or not os.path.isdir(workspace_path):
            return "unknown"
        if os.path.exists(os.path.join(workspace_path, "pom.xml")) or os.path.exists(os.path.join(workspace_path, "build.gradle")):
            return "java"
        if os.path.exists(os.path.join(workspace_path, "package.json")):
            return "javascript"
        if os.path.exists(os.path.join(workspace_path, "requirements.txt")) or os.path.exists(os.path.join(workspace_path, "pyproject.toml")) or os.path.exists(os.path.join(workspace_path, "setup.py")):
            return "python"
        if os.path.exists(os.path.join(workspace_path, "Gemfile")):
            return "ruby"
        if os.path.exists(os.path.join(workspace_path, "Cargo.toml")):
            return "rust"
        if os.path.exists(os.path.join(workspace_path, "go.mod")):
            return "go"
        return "unknown"

def extract_library_versions(goal: str) -> List[Tuple[str, str]]:
    """
    Parses a goal string to extract library names and versions.
    Looks for patterns like:
      - Apache Ignite 2.18
      - ignite-core:2.18.0
      - spring-boot 3.2.0
      - Quarkus 3.12.0
      - package@version (npm style)
      - package==version (pypi style)
    Returns list of canonical (library_name, version_string) tuples.
    """
    results = []
    if not goal:
        return results

    # Normalize package coordinates first e.g. org.apache.ignite:ignite-core:2.18.0
    direct_coords = re.findall(r"([a-zA-Z0-9_\-\.]+:[a-zA-Z0-9_\-\.]+):([0-9\.]+)", goal)
    for lib, ver in direct_coords:
        results.append((lib, ver))

    # Pattern for name@version or name==version
    package_versions = re.findall(r"\b([a-zA-Z0-9_\-\.]+)(?:==|@)([0-9\.]+)\b", goal)
    for lib, ver in package_versions:
        results.append((lib, ver))

    # Pattern for "name version" or "name v.version" or "name vversion"
    name_space_version = re.findall(r"\b(Apache Ignite|Quarkus|ActiveMQ Artemis|Spring Boot|spring-boot|Spring|ignite|artemis|qpid-jms|qpid-jms-client)\s+(?:v)?([0-9\.]+)\b", goal, re.IGNORECASE)
    for lib, ver in name_space_version:
        results.append((lib.lower().replace(" ", "-"), ver))

    # Canonicalize and deduplicate
    seen = set()
    seen_canonical = set()
    deduped = []
    for lib, ver in results:
        name_lower = lib.lower().strip()
        if name_lower in COMMON_ALIASES:
            g, a = COMMON_ALIASES[name_lower]
            canonical = f"{g}:{a}"
        elif name_lower == "apache-ignite":
            canonical = "org.apache.ignite:ignite-core"
        elif name_lower == "activemq-artemis":
            canonical = "org.apache.activemq:artemis-server"
        else:
            canonical = name_lower

        # Normalize version (e.g. "2.18" -> "2.18.0")
        v_clean = ver.strip().strip(".")
        parts = v_clean.split(".")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            v_clean = f"{v_clean}.0"

        key = (canonical, v_clean)
        if key not in seen:
            seen.add(key)
            deduped.append(key)
        seen_canonical.add(canonical)

    # Bare (unversioned) mentions of known technologies - e.g. "Redhat qpid MRG broker"
    # names Qpid without a version number, so the passes above never see it. Only checks
    # against COMMON_ALIASES (a small, deliberately curated table) to keep this precise -
    # this is what lets the structural skill-coverage check below fire even when no
    # version is mentioned at all. Skipped for libraries already matched with a real
    # version, so a goal mixing "Ignite 2.18.0 ... ignite cache" doesn't produce a
    # spurious second "unspecified" entry alongside the real one.
    for alias_key, (g, a) in COMMON_ALIASES.items():
        canonical = f"{g}:{a}"
        if canonical in seen_canonical:
            continue
        if re.search(rf"\b{re.escape(alias_key)}\b", goal, re.IGNORECASE):
            key = (canonical, "unspecified")
            if key not in seen:
                seen.add(key)
                deduped.append(key)
            seen_canonical.add(canonical)

    return deduped

class GapReport:
    """Consolidated representation of knowledge gap check results."""
    def __init__(self) -> None:
        self.gaps: List[Dict[str, Any]] = []
        self.user_confirmed: bool = False

    @property
    def has_gaps(self) -> bool:
        return len(self.gaps) > 0

    def add_gap(self, library: str, version: str, release_date: Optional[datetime], risk_level: str, reason: str) -> None:
        self.gaps.append({
            "library": library,
            "version": version,
            "release_date": release_date.isoformat() if release_date else None,
            "risk_level": risk_level,
            "reason": reason
        })

    def format_report(self) -> str:
        if not self.has_gaps:
            return "No knowledge gaps detected."
        lines = [
            "⚠️  KNOWLEDGE GUARD RISK DETECTED",
            "──────────────────────────────────────────────────────"
        ]
        for g in self.gaps:
            if g["version"] == "unspecified":
                lines.append(f"Library  : {g['library']} (no specific version mentioned)")
                lines.append(f"Risk Level: {g['risk_level']}")
            else:
                lines.append(f"Library  : {g['library']} (version {g['version']})")
                date_str = g["release_date"][:10] if g["release_date"] else "Unknown"
                lines.append(f"Released : {date_str}  |  Risk Level: {g['risk_level']}")
            lines.append(f"Reason   : {g['reason']}")
            lines.append("")
        lines.append("──────────────────────────────────────────────────────")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_gaps": self.has_gaps,
            "user_confirmed": self.user_confirmed,
            "gaps": self.gaps
        }

class KnowledgeGuard:
    """Subsystem checking for missing skills and model cutoff temporal mismatches."""
    def __init__(self, skills_dir: str, cutoff_date_str: str, offline: bool = False, memory_dir: Optional[str] = None) -> None:
        self.skills_dir = os.path.abspath(skills_dir)
        self.cutoff_date = parse_iso_datetime(cutoff_date_str) or datetime(2023, 12, 1, tzinfo=timezone.utc)
        self.offline = offline
        self.cache = None
        if memory_dir:
            self.cache = KnowledgeCache(memory_dir)

    def check_goal(self, goal: str, workspace_path: str = "") -> GapReport:
        report = GapReport()
        libs = extract_library_versions(goal)
        if not libs:
            return report

        # Detect ecosystem from workspace if possible
        stack = RegistryAdapterFactory.detect_stack(workspace_path)
        if stack == "unknown" and libs:
            # Fallback guessing ecosystem
            stack = "java"  # Default assumption for maven/gradle coordinates

        adapter = RegistryAdapterFactory.from_stack(stack)

        for lib, ver in libs:
            # 1. Structural Gap Check (Missing Skills)
            skill_found = False
            if os.path.exists(self.skills_dir):
                aliases = [lib]
                if ":" in lib:
                    aliases.extend(lib.split(":"))
                for k, v in COMMON_ALIASES.items():
                    if f"{v[0]}:{v[1]}" == lib:
                        aliases.append(k)
                if "ignite" in lib.lower():
                    aliases.extend(["apache-ignite", "ignite"])
                if "artemis" in lib.lower():
                    aliases.extend(["activemq-artemis", "artemis"])

                for folder in os.listdir(self.skills_dir):
                    if any(a.lower().replace(":", "-").replace("/", "-") in folder.lower() for a in aliases):
                        skill_found = True
                        break

            if ver == "unspecified":
                # No version was mentioned for this library, so there's nothing to check
                # for temporal (post-cutoff) risk - only report a gap if there's also no
                # skill coverage for it at all.
                if not skill_found:
                    reason = f"'{lib}' is mentioned in the goal but has no verified skill file - Kriya has no curated guidance for it."
                    report.add_gap(lib, "unspecified", None, "MEDIUM", reason)
                continue

            # 2. Temporal Cutoff Check
            rel_date = adapter.get_release_date(lib, ver, offline=self.offline, cache=self.cache)
            if rel_date:
                # Normalize both datetimes to timezone-aware UTC for comparison
                cutoff_utc = self.cutoff_date.astimezone(timezone.utc)
                rel_utc = rel_date.astimezone(timezone.utc)
                if rel_utc > cutoff_utc:
                    reason = f"Released on {rel_utc.date().isoformat()}, which is after the model cutoff of {cutoff_utc.date().isoformat()}."
                    risk = "HIGH" if not skill_found else "MEDIUM"
                    report.add_gap(lib, ver, rel_date, risk, reason)
                    continue

            # If no release date is found or temporal check passes, check structural
            if not skill_found:
                reason = f"No verified skill file found in the skills directory for '{lib}'."
                report.add_gap(lib, ver, None, "MEDIUM", reason)

        return report

    def generate_skill_template(self, library: str, version: Optional[str]) -> str:
        """Scaffolds a new skill directory template under skills/."""
        has_version = version not in (None, "unspecified")
        clean_lib = library.lower().replace(":", "-").replace("/", "-")
        skill_name = f"{clean_lib}-{version}" if has_version else clean_lib
        target_dir = os.path.join(self.skills_dir, skill_name)
        os.makedirs(target_dir, exist_ok=True)

        # 1. skill.yaml
        version_line = f'version: "{version}"\n' if has_version else ""
        supported_versions = f">={version}" if has_version else "*"
        description = f"Custom engineering skill for {library} version {version}" if has_version else f"Custom engineering skill for {library}"
        yaml_content = f"""name: {clean_lib}
description: {description}
{version_line}# supported_versions defines the semver range of library versions this skill supports.
# Example: ">=2.15.0 <3.0.0" (use "*" if not version-specific)
supported_versions: "{supported_versions}"
tags:
  - {clean_lib}
  - java
"""
        with open(os.path.join(target_dir, "skill.yaml"), "w", encoding="utf-8") as f:
            f.write(yaml_content)

        # 2. rules.txt
        rules_content = """# Coding rules and gotchas for this library version.
# Each line is read as a rule to direct model coding behavior.
# Example:
# - Always disable cache persistence in Ignite Configuration when running unit tests.
# - Do not use deprecated client factory classes.
"""
        with open(os.path.join(target_dir, "rules.txt"), "w", encoding="utf-8") as f:
            f.write(rules_content)

        # 3. instructions.md
        title = f"# {library} Version {version} Guidelines" if has_version else f"# {library} Guidelines"
        version_note = f"introduced in version {version}" if has_version else "relevant to this library"
        instructions_content = f"""{title}

## API Differences and Setup Steps
Provide step-by-step setup guides here. Explain any API changes or parameters {version_note}.

## Key Code Snippets
Include Markdown code blocks to guide code generation.

## Official Resources
- Release Notes: https://search.maven.org/artifact/{library}
"""
        with open(os.path.join(target_dir, "instructions.md"), "w", encoding="utf-8") as f:
            f.write(instructions_content)

        # 4. Create examples folder & blank template
        examples_dir = os.path.join(target_dir, "examples")
        os.makedirs(examples_dir, exist_ok=True)
        example_file = os.path.join(examples_dir, "Example.java")
        with open(example_file, "w", encoding="utf-8") as f:
            f.write(f"// Example boilerplate for {library}{' ' + version if has_version else ''}\n")

        return target_dir
