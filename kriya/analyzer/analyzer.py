import ast
import fnmatch
import hashlib
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Extensions to languages map
EXTENSION_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".jsx": "React JS",
    ".tsx": "React TS",
    ".go": "Go",
    ".java": "Java",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C Header",
    ".php": "PHP",
    ".html": "HTML",
    ".css": "CSS",
    ".sh": "Shell Script",
    ".kt": "Kotlin",
    ".swift": "Swift"
}

def chunk_file_with_metadata_headers(content: str, rel_path: str) -> List[Dict[str, Any]]:
    _, ext = os.path.splitext(rel_path)
    ext = ext.lower()
    
    chunks = []
    
    if ext == ".py":
        package = rel_path.replace("/", ".").replace(".py", "")
        try:
            tree = ast.parse(content, filename=rel_path)
        except Exception as e:
            logger.debug(f"Failed to parse AST for '{rel_path}', falling back to generic chunking: {e}")
            tree = None

        if tree is not None:
            lines = content.splitlines()
            
            # 1. Module Declarations
            module_decls = []
            for node in tree.body:
                if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    start_line = getattr(node, "lineno", 1)
                    end_line = getattr(node, "end_lineno", start_line)
                    module_decls.extend(lines[start_line - 1:end_line])
            if module_decls:
                chunks.append({
                    "text": f"File: {rel_path}\nModule: {package}\n=== Module Declarations ===\n" + "\n".join(module_decls),
                    "start": 1,
                    "end": len(module_decls)
                })

            # Map children to parents
            parent_map = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parent_map[child] = parent

            # 2. Walk tree to find classes and functions
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    class_name = node.name
                    docstring = ast.get_docstring(node) or ""
                    start_line = node.lineno
                    end_line = start_line
                    if node.body:
                        end_line = getattr(node.body[0], "lineno", start_line) - 1
                        if end_line < start_line:
                            end_line = start_line
                    class_sig = lines[start_line - 1:end_line]
                    header = f"File: {rel_path}\nModule: {package}\nClass: {class_name}\nDocstring: {docstring}\n=== Class Declaration ===\n"
                    chunks.append({
                        "text": header + "\n".join(class_sig) + (f"\n{docstring}" if docstring else ""),
                        "start": start_line,
                        "end": end_line
                    })
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parent = parent_map.get(node)
                    while parent is not None and not isinstance(parent, (ast.ClassDef, ast.Module)):
                        parent = parent_map.get(parent)
                    
                    parent_class_name = ""
                    parent_class_doc = ""
                    if isinstance(parent, ast.ClassDef):
                        parent_class_name = parent.name
                        parent_class_doc = ast.get_docstring(parent) or ""
                    
                    method_name = node.name
                    docstring = ast.get_docstring(node) or ""
                    start_line = node.lineno
                    end_line = getattr(node, "end_lineno", start_line)
                    method_lines = lines[start_line - 1:end_line]
                    
                    header = f"File: {rel_path}\nModule: {package}\n"
                    if parent_class_name:
                        header += f"Class: {parent_class_name}\nClass Docstring: {parent_class_doc}\n"
                    header += f"Method: {method_name}\nDocstring: {docstring}\n=== Method Body ===\n"
                    chunks.append({
                        "text": header + "\n".join(method_lines),
                        "start": start_line,
                        "end": end_line
                    })
            
    elif ext == ".java":
        pkg_match = re.search(r"package\s+([\w\.]+);", content)
        package = pkg_match.group(1) if pkg_match else "default"
        
        lines = content.splitlines()
        current_class = ""
        current_class_javadoc = ""
        
        i = 0
        while i < len(lines):
            line = lines[i]
            line_strip = line.strip()
            
            class_match = re.search(r"\bclass\s+(\w+)", line_strip)
            if class_match:
                current_class = class_match.group(1)
                javadoc_lines = []
                j = i - 1
                if j >= 0 and "*/" in lines[j]:
                    while j >= 0:
                        javadoc_lines.insert(0, lines[j])
                        if "/**" in lines[j]:
                            break
                        j -= 1
                current_class_javadoc = "\n".join(javadoc_lines).strip()
                
                header = f"File: {rel_path}\nPackage: {package}\nClass: {current_class}\nClass Javadoc: {current_class_javadoc}\n=== Class Declaration ===\n"
                chunks.append({
                    "text": header + line,
                    "start": i + 1,
                    "end": i + 1
                })
                i += 1
                continue
                
            method_match = re.search(r'(?:public|protected|private|static|\s)+[\w<>]+\s+(\w+)\s*\([^\)]*\)(?:\s+throws\s+[\w\s,]+)?\s*\{', line_strip)
            if method_match and current_class:
                method_name = method_match.group(1)
                if method_name not in {"class", "interface", "enum", "if", "for", "while", "switch", "catch"}:
                    method_lines = [line]
                    start_idx = i
                    
                    brace_count = 1
                    i += 1
                    while i < len(lines) and brace_count > 0:
                        line = lines[i]
                        method_lines.append(line)
                        brace_count += line.count("{") - line.count("}")
                        i += 1
                        
                    header = f"File: {rel_path}\nPackage: {package}\nClass: {current_class}\nClass Javadoc: {current_class_javadoc}\nMethod: {method_name}\n=== Method Body ===\n"
                    chunks.append({
                        "text": header + "\n".join(method_lines),
                        "start": start_idx + 1,
                        "end": i
                    })
                    continue
            i += 1
            
    elif ext == ".xml":
        import xml.etree.ElementTree as ET
        try:
            cleaned_content = re.sub(r'\sxmlns="[^"]+"', '', content)
            cleaned_content = re.sub(r'\sxmlns:[^=]+="[^"]+"', '', cleaned_content)
            cleaned_content = re.sub(r'<beans[^>]*>', '<beans>', cleaned_content)
            
            root = ET.fromstring(cleaned_content)
            for idx, bean in enumerate(root.findall(".//bean"), 1):
                bean_id = bean.get("id") or bean.get("name") or f"bean_{idx}"
                bean_xml = ET.tostring(bean, encoding="utf-8").decode("utf-8")
                header = f"File: {rel_path}\nSpring Bean: {bean_id}\n=== Bean Configuration ===\n"
                chunks.append({
                    "text": header + bean_xml,
                    "start": 1,
                    "end": 1
                })
        except Exception as e:
            logger.debug(f"Failed to parse Spring XML bean config for '{rel_path}', falling back to generic chunking: {e}")

    if not chunks:
        chunks = chunk_file_syntactically(content)
        for c in chunks:
            c["text"] = f"File: {rel_path}\n=== Code Content ===\n" + c["text"]
            
    return chunks

def chunk_file_syntactically(content: str, max_lines: int = 100, overlap: int = 15) -> List[Dict[str, Any]]:
    """Chunks a code file into blocks of max_lines with overlap, aligning boundaries with classes/methods."""
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return [{"text": content, "start": 1, "end": len(lines)}]

    chunks = []
    i = 0
    while i < len(lines):
        end = min(i + max_lines, len(lines))
        
        boundary = end
        if end < len(lines):
            # Scan forward in the overlap window to find the earliest declaration boundary
            for j in range(max(end - overlap, i), end):
                line = lines[j].strip()
                if (line.startswith("class ") or 
                    line.startswith("def ") or 
                    line.startswith("public ") or 
                    line.startswith("private ") or
                    line.startswith("@")):
                    boundary = j
                    break
        
        chunk_lines = lines[i:boundary]
        chunk_text = "\n".join(chunk_lines)
        chunks.append({
            "text": chunk_text,
            "start": i + 1,
            "end": boundary
        })
        
        # Advance with overlap
        i = max(boundary, i + max_lines - overlap)
        if i >= len(lines):
            break
            
    return chunks

class RepositoryModel(BaseModel):
    root_path: str = Field(description="Absolute path to the repository root.")
    languages: Dict[str, float] = Field(
        default_factory=dict, 
        description="Detected languages mapped to their percentage distribution by file count."
    )
    frameworks: List[str] = Field(default_factory=list, description="Detected frameworks.")
    architecture: str = Field(default="Unknown", description="Inferred codebase architecture style.")
    dependencies: List[str] = Field(default_factory=list, description="Key discovered project dependencies.")
    testing_frameworks: List[str] = Field(default_factory=list, description="Testing framework identifiers.")
    project_structure: Dict[str, Any] = Field(default_factory=dict, description="Basic folder structure hierarchy.")
    coding_style: Dict[str, Any] = Field(default_factory=dict, description="Detected coding style metrics.")


def parse_gitignore(root_path: str) -> List[str]:
    patterns = []
    gitignore_path = os.path.join(root_path, ".gitignore")
    if os.path.exists(gitignore_path):
        try:
            with open(gitignore_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
        except Exception as e:
            logger.debug(f"Failed to read '.gitignore' at '{gitignore_path}': {e}")
    return patterns

def is_ignored(filepath: str, root_path: str, gitignore_patterns: List[str]) -> bool:
    rel_path = os.path.relpath(filepath, root_path)
    parts = rel_path.split(os.sep)
    system_ignores = {"target", "build", "node_modules", "dist", ".git", ".venv", "venv", "__pycache__", "obj", "bin"}
    for p in parts:
        if p in system_ignores or p.startswith("."):
            return True
    for pat in gitignore_patterns:
        if pat.endswith("/"):
            pat = pat[:-1]
        if fnmatch.fnmatch(rel_path, pat) or any(fnmatch.fnmatch(part, pat) for part in parts):
            return True
    return False

class RepositoryAnalyzer:
    """Analyzes workspace directory to extract language, frameworks, architecture and dependencies."""

    def __init__(self, root_path: str) -> None:
        self.root_path = os.path.abspath(root_path)

    def analyze(self) -> RepositoryModel:
        """Run analysis on the repository and return a RepositoryModel."""
        if not os.path.exists(self.root_path):
            raise FileNotFoundError(f"Root path '{self.root_path}' does not exist.")

        model = RepositoryModel(root_path=self.root_path)
        
        # 1. Walk repository and count files/extensions
        file_counts = {}
        total_files = 0
        file_list = []
        directories = set()

        ignore_dirs = {
            ".git", ".venv", "venv", "node_modules", "__pycache__",
            ".pytest_cache", "build", "dist", ".egg-info", "eggs", "bin", "obj",
            # Kriya's own reserved paths.* directories (default names - a
            # differently-configured project's own paths.skills/memory/logs
            # would need this list threaded through from AppConfig to match,
            # same known limitation as _has_any_py_file()'s hardcoded
            # .kriya exclusion elsewhere in this codebase). These are
            # Kriya's own bookkeeping, never the user's application
            # structure, and must never be presented to the model as if
            # they were - confirmed live, 2026-08-07: an Architect prompt
            # reported an empty "skills" directory as an existing top-level
            # project folder, and the model concluded a Django app already
            # existed there (plus a manage.py it never actually saw), then
            # tried to extend a project that was never created instead of
            # building one from scratch.
            "skills", "memory", "logs",
        }

        for root, dirs, files in os.walk(self.root_path):
            # Modify dirs in-place to skip ignored directories
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]

            rel_path = os.path.relpath(root, self.root_path)
            # Only report a top-level folder as real project structure if it
            # actually contains at least one real (non-dotfile) file - an
            # empty directory being WALKED INTO used to be enough to report
            # it as an existing "top_level_folder" regardless of whether
            # anything was ever in it, which is exactly the false signal
            # that caused the skills/manage.py hallucination above.
            real_files_here = [f for f in files if not f.startswith(".")]
            if rel_path != "." and real_files_here:
                directories.add(rel_path.split(os.sep)[0])

            for file in files:
                if file.startswith("."):
                    continue
                _, ext = os.path.splitext(file)
                ext = ext.lower()
                
                lang = EXTENSION_MAP.get(ext)
                if lang:
                    file_counts[lang] = file_counts.get(lang, 0) + 1
                    total_files += 1
                
                file_list.append(os.path.join(root, file))

        # Calculate language percentages
        if total_files > 0:
            model.languages = {lang: round((count / total_files) * 100, 2) for lang, count in file_counts.items()}

        # 2. Check dependencies & frameworks
        self._detect_dependencies_and_frameworks(model)

        # 3. Detect architecture signature
        self._detect_architecture(model, directories)

        # 4. Check coding style heuristics on a subset of files
        self._detect_coding_style(model, file_list)

        # 5. Extract structural summary
        model.project_structure = {
            "top_level_folders": list(directories),
            "total_files_indexed": len(file_list)
        }

        return model

    def _detect_dependencies_and_frameworks(self, model: RepositoryModel) -> None:
        """Heuristically parses lockfiles/requirements to identify packages and frameworks."""
        frameworks = set()
        dependencies = set()
        testing = set()

        # Python requirement checks
        pyproject_path = os.path.join(self.root_path, "pyproject.toml")
        req_path = os.path.join(self.root_path, "requirements.txt")

        # Node / JS requirement checks
        package_json_path = os.path.join(self.root_path, "package.json")

        # Java checks
        pom_path = os.path.join(self.root_path, "pom.xml")
        gradle_path = os.path.join(self.root_path, "build.gradle")

        # Go checks
        go_mod_path = os.path.join(self.root_path, "go.mod")

        # Rust checks
        cargo_path = os.path.join(self.root_path, "Cargo.toml")

        # 1. Parse pyproject.toml
        if os.path.exists(pyproject_path):
            frameworks.add("Setuptools / Poetry (Python)")
            try:
                with open(pyproject_path, "r", errors="replace") as f:
                    content = f.read()
                    # Check packages
                    for pkg in ["fastapi", "django", "flask", "pyramid", "pytest", "black", "pydantic", "jinja2", "openai"]:
                        if pkg in content.lower():
                            dependencies.add(pkg)
            except Exception as e:
                logger.debug(f"Failed to parse '{pyproject_path}': {e}")

        # 2. Parse requirements.txt
        if os.path.exists(req_path):
            try:
                with open(req_path, "r", errors="replace") as f:
                    for line in f:
                        line = line.strip().lower()
                        if not line or line.startswith("#"):
                            continue
                        # Remove version specifiers
                        pkg = re.split(r"[=<>]", line)[0].strip()
                        dependencies.add(pkg)
            except Exception as e:
                logger.debug(f"Failed to parse '{req_path}': {e}")

        # 3. Parse package.json
        if os.path.exists(package_json_path):
            frameworks.add("Node.js")
            try:
                with open(package_json_path, "r", errors="replace") as f:
                    data = json.load(f)
                    all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                    for dep in all_deps.keys():
                        dependencies.add(dep)
            except Exception as e:
                logger.debug(f"Failed to parse '{package_json_path}': {e}")

        # 4. Check specific framework markers
        for dep in list(dependencies):
            if dep in ["fastapi", "uvicorn"]:
                frameworks.add("FastAPI")
            elif dep == "django":
                frameworks.add("Django")
            elif dep == "flask":
                frameworks.add("Flask")
            elif dep in ["react", "react-dom"]:
                frameworks.add("React")
            elif dep == "next":
                frameworks.add("Next.js")
            elif dep == "vue":
                frameworks.add("Vue")
            elif dep == "express":
                frameworks.add("Express")
            
            # Test frameworks
            if dep in ["pytest", "nose", "unittest"]:
                testing.add(dep)
            elif dep in ["jest", "mocha", "cypress"]:
                testing.add(dep)

        # 5. Check Java markers
        if os.path.exists(pom_path):
            frameworks.add("Maven (Java)")
            try:
                with open(pom_path, "r", errors="replace") as f:
                    content = f.read()
                    if "spring-boot" in content:
                        frameworks.add("Spring Boot")
                    if "junit" in content:
                        testing.add("JUnit")
                    # Extract maven dependencies
                    deps = re.findall(r"<artifactId>([^<]+)</artifactId>", content)
                    for d in deps:
                        dependencies.add(d.strip())
            except Exception as e:
                logger.debug(f"Failed to parse '{pom_path}': {e}")

        if os.path.exists(gradle_path):
            frameworks.add("Gradle (Java)")
            try:
                with open(gradle_path, "r", errors="replace") as f:
                    content = f.read()
                    if "junit" in content:
                        testing.add("JUnit")
                    # Extract gradle dependencies (e.g. implementation 'org.apache.ignite:ignite-core:2.18.0')
                    deps = re.findall(r"(?:implementation|compileOnly|runtimeOnly|testImplementation)\s+['\"](?:[^:]+:)?([^:']+)['\"]", content)
                    for d in deps:
                        dependencies.add(d.strip())
                    if "spring-boot" in content:
                        frameworks.add("Spring Boot")
                    if "junit" in content:
                        testing.add("JUnit")
            except Exception as e:
                logger.debug(f"Failed to parse '{gradle_path}': {e}")

        # 6. Check Go markers
        if os.path.exists(go_mod_path):
            frameworks.add("Go Modules")
            testing.add("go test")

        # 7. Check Rust markers
        if os.path.exists(cargo_path):
            frameworks.add("Cargo (Rust)")
            testing.add("cargo test")

        model.frameworks = sorted(list(frameworks))
        model.dependencies = sorted(list(dependencies))
        model.testing_frameworks = sorted(list(testing))

    def _detect_architecture(self, model: RepositoryModel, directories: set) -> None:
        """Determines design pattern or architectural structure of code base."""
        dirs_lower = {d.lower() for d in directories}
        
        mvc_markers = {"controllers", "models", "views"}
        clean_markers = {"domain", "infra", "infrastructure", "interfaces", "application", "usecase", "usecases"}
        
        if mvc_markers.intersection(dirs_lower):
            model.architecture = "MVC (Model-View-Controller)"
        elif clean_markers.intersection(dirs_lower):
            model.architecture = "Clean Architecture / DDD"
        elif "src" in dirs_lower or "lib" in dirs_lower:
            model.architecture = "Standard Source/Library layout"
        else:
            model.architecture = "Flat/Modular Package layout"

    def _detect_coding_style(self, model: RepositoryModel, file_list: List[str]) -> None:
        """Heuristically detects indentation, naming convention, and general styles."""
        indent_spaces = 0
        indent_tabs = 0
        total_analyzed = 0
        
        # Analyze first 5 code files
        code_files = [f for f in file_list if os.path.splitext(f)[1] in [".py", ".js", ".ts", ".go", ".java", ".rs"]]
        
        for filepath in code_files[:5]:
            try:
                with open(filepath, "r", errors="replace") as f:
                    lines = f.readlines()
                    
                total_analyzed += 1
                for line in lines[:100]: # check first 100 lines
                    if line.startswith(" "):
                        indent_spaces += 1
                    elif line.startswith("\t"):
                        indent_tabs += 1
            except Exception as e:
                logger.debug(f"Failed to sample '{filepath}' for coding style: {e}")

        indentation = "Unknown"
        if total_analyzed > 0:
            if indent_spaces > indent_tabs * 2:
                indentation = "Spaces"
            elif indent_tabs > indent_spaces * 2:
                indentation = "Tabs"
            elif indent_spaces > 0 or indent_tabs > 0:
                indentation = "Mixed"

        model.coding_style = {
            "indentation": indentation,
            "sample_files_analyzed": total_analyzed
        }

        return model

    async def index_repository(
        self, 
        cfg: Any, 
        changed: bool = False,
        force: bool = False,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> None:
        """Walks the repository, chunks code files, generates semantic embeddings, and stores them in LocalVectorStore."""
        from kriya.memory.vector import LocalVectorStore, OllamaEmbeddingClient
        
        # 1. Resolve storage paths
        vector_index_path = os.path.join(cfg.paths.memory, "vector_index.db")
        store = LocalVectorStore(vector_index_path)
        client = OllamaEmbeddingClient(base_url=cfg.embedding.base_url, model=cfg.embedding.model)
        
        from kriya.analyzer.graph import DependencyGraph
        db_path = os.path.join(cfg.paths.memory, "dependency_graph.db")
        graph = DependencyGraph(db_path)

        # Probe model dimensions dynamically and verify
        test_emb = await client.get_embedding("test")
        detected_dim = len(test_emb)
        
        try:
            store.verify_model(cfg.embedding.model, detected_dim)
        except ValueError as e:
            if force:
                logger.info("Forcing re-index due to model/dimension mismatch. Wiping existing vector index...")
                cursor = store.conn.cursor()
                cursor.execute("DELETE FROM vector_chunks")
                if store.use_fts:
                    cursor.execute("DELETE FROM fts_chunks")
                else:
                    cursor.execute("DELETE FROM fts_chunks_fallback")
                cursor.execute("DELETE FROM file_metadata")
                store.conn.commit()
            else:
                store.close()
                graph.close()
                raise e
        
        # 2. Find target files (respecting nested gitignores and system ignore filters)
        target_extensions = {".py", ".java", ".xml", ".rb"}
        
        files_to_index = []
        gitignore_cache = {self.root_path: parse_gitignore(self.root_path)}

        for root, dirs, files in os.walk(self.root_path):
            parent = os.path.dirname(root)
            current_patterns = list(gitignore_cache.get(parent, gitignore_cache[self.root_path]))
            
            local_gitignore = os.path.join(root, ".gitignore")
            if os.path.exists(local_gitignore) and root != self.root_path:
                try:
                    with open(local_gitignore, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                rel_dir = os.path.relpath(root, self.root_path)
                                if rel_dir != ".":
                                    current_patterns.append(os.path.join(rel_dir, line))
                                else:
                                    current_patterns.append(line)
                except Exception as e:
                    logger.debug(f"Failed to read '.gitignore' at '{local_gitignore}': {e}")
            gitignore_cache[root] = current_patterns

            dirs[:] = [d for d in dirs if not is_ignored(os.path.join(root, d), self.root_path, current_patterns)]
            for file in files:
                filepath = os.path.join(root, file)
                if is_ignored(filepath, self.root_path, current_patterns):
                    continue
                _, ext = os.path.splitext(file)
                if ext.lower() in target_extensions:
                    files_to_index.append(filepath)
                    
        if changed:
            import subprocess
            try:
                res = subprocess.run(["git", "diff", "--name-only"], cwd=self.root_path, capture_output=True, text=True)
                res_untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=self.root_path, capture_output=True, text=True)
                if res.returncode != 0 or res_untracked.returncode != 0:
                    # Confirmed live: on a non-git directory, both commands fail
                    # (returncode != 0, not an exception) - the OLD code silently
                    # left git_files empty either way, which filtered
                    # files_to_index down to NOTHING with no indication this
                    # happened because --changed genuinely couldn't be honored,
                    # rather than because there really were zero changes. Warn
                    # clearly and fall back to indexing everything instead of
                    # silently indexing nothing.
                    stderr = (res.stderr or res_untracked.stderr or "").strip()
                    logger.warning(
                        f"--changed requires a git repository, but git reported an error at "
                        f"'{self.root_path}'{f': {stderr}' if stderr else ''} - indexing all files "
                        f"instead of only changed ones."
                    )
                else:
                    git_files = set()
                    for line in res.stdout.splitlines():
                        if line.strip():
                            git_files.add(os.path.abspath(os.path.join(self.root_path, line.strip())))
                    for line in res_untracked.stdout.splitlines():
                        if line.strip():
                            git_files.add(os.path.abspath(os.path.join(self.root_path, line.strip())))
                    files_to_index = [f for f in files_to_index if os.path.abspath(f) in git_files]
            except Exception as e:
                logger.warning(f"Failed to query git for changes: {e} - indexing all files instead of only changed ones.")

        total_files = len(files_to_index)
        logger.info(f"Discovered {total_files} files for semantic indexing.")
        
        # 3. Index files incrementally
        current_rel_paths = set()
        for idx, filepath in enumerate(files_to_index, 1):
            rel_path = os.path.relpath(filepath, self.root_path)
            current_rel_paths.add(rel_path)
            
            try:
                mtime = os.path.getmtime(filepath)
                cached_mtime = store.file_metadata.get(rel_path, {}).get("mtime")
                cached_graph_mtime = graph.get_cached_mtime(rel_path)
                
                # Fast path skip: mtime check
                if not force and cached_mtime == mtime and cached_graph_mtime == mtime:
                    if progress_callback:
                         progress_callback(f"{rel_path} [Up-to-date]", idx, total_files)
                    continue

                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                
                # Compute SHA-1 hash
                file_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()
                cached_hash = store.file_metadata.get(rel_path, {}).get("hash")
                cached_graph_hash = graph.get_cached_hash(rel_path)
                
                # Resilient skip: hash check
                if not force and cached_hash == file_hash and cached_graph_hash == file_hash:
                    store.file_metadata[rel_path] = {"mtime": mtime, "hash": file_hash}
                    if progress_callback:
                         progress_callback(f"{rel_path} [Up-to-date]", idx, total_files)
                    continue

                if progress_callback:
                    progress_callback(rel_path, idx, total_files)

                # Wrap changes in explicit transaction
                with store.conn, graph.conn:
                    # Clear old chunks first to support re-indexing clean
                    store.remove_file(rel_path)
                    
                    # Index in dependency graph
                    graph.index_file(rel_path, content, mtime, file_hash)
                    
                    # Chunk file with metadata headers
                    chunks = chunk_file_with_metadata_headers(content, rel_path)
                    
                    chunk_texts = [c["text"] for c in chunks if c["text"].strip()]
                    if chunk_texts:
                        # Generate all embeddings concurrently
                        embs = await client.get_embeddings(chunk_texts)
                        
                        for chunk_idx, (chunk_text, emb) in enumerate(zip(chunk_texts, embs, strict=True)):
                            store.add_document(
                                filepath=rel_path,
                                text=chunk_text,
                                embedding=emb,
                                chunk_index=chunk_idx,
                                model_name=cfg.embedding.model,
                                dimensions=len(emb)
                            )
                    # Store new cache metadata (including hash and mtime)
                    store.file_metadata[rel_path] = {"mtime": mtime, "hash": file_hash}
            except Exception as e:
                logger.error(f"Failed to index file {rel_path}: {e}")
                
        # Remove deleted files from cached index
        cached_files = list(store.file_metadata.keys())
        for cached_file in cached_files:
            if cached_file not in current_rel_paths:
                logger.info(f"Removing deleted file from index cache: {cached_file}")
                with store.conn, graph.conn:
                    store.remove_file(cached_file)
                    graph.clear_file(cached_file)
                
        # 4. Save persistent cache index
        store.save()
        logger.info("Semantic repository indexing completed.")
        store.close()
        graph.close()
        
        # 5. Auto-Generate Codebase Conventions Skill
        repo_slug = os.path.basename(self.root_path).lower().strip(".")
        if not repo_slug:
            repo_slug = "root"
            
        skills_dir = getattr(cfg.paths, "skills", "./skills")
        auto_skill_dir = os.path.join(skills_dir, f"auto-{repo_slug}")

        if not os.path.exists(auto_skill_dir):
            import click
            import yaml

            from kriya.skills.skill import is_accidental_shared_skills_write
            if is_accidental_shared_skills_write(skills_dir, self.root_path):
                click.secho(
                    f"\nWarning: this project's config doesn't set paths.skills, so it's about to "
                    f"write a new auto-generated skill into Kriya's own SHARED install skills "
                    f"directory ({os.path.abspath(skills_dir)}) instead of a project-local one - "
                    f"every other project using Kriya would inherit this. If that's not intended, "
                    f"stop now and set paths.skills in this project's kriya.yaml, e.g. \"./skills\".",
                    fg="red", bold=True, err=True,
                )

            from kriya.agents.extractor import ConventionsExtractorAgent
            from kriya.core.llm import LLMClient

            # This whole block is narration/status about the auto-skill-
            # bootstrap side effect, not the `analyze` command's actual JSON
            # payload (already printed to stdout by cli.py before this runs)
            # - found via a test written to check the SAME stdout-pollution
            # bug class already fixed in cli.py's prompt generate/analyze/
            # review, which this block, living in analyzer.py rather than
            # cli.py, wasn't touched by. Everything here goes to stderr so it
            # can't corrupt a downstream JSON consumer piping analyze's stdout.
            click.secho("\nAnalyzing repository style guidelines and patterns...", bold=True, fg="yellow", err=True)
            
            # Gather sample code files (first 3 files)
            samples = []
            for fp in files_to_index[:3]:
                rel = os.path.relpath(fp, self.root_path)
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        head = f.read(1500)
                    samples.append(f"=== File: {rel} ===\n{head}\n")
                except Exception as e:
                    logger.debug(f"Failed to read sample file '{fp}' for auto-skill convention extraction: {e}")
            samples_str = "\n".join(samples)
            
            try:
                model = self.analyze()
                struct_str = model.model_dump_json(indent=2)
                
                llm = LLMClient(cfg)
                extractor = ConventionsExtractorAgent("extractor", llm)
                
                import sys
                def extractor_stream(token: str):
                    click.echo(token, nl=False, err=True)
                    sys.stderr.flush()

                res = await extractor.extract_conventions(struct_str, samples_str, stream_callback=extractor_stream)
                click.echo(err=True)
                
                os.makedirs(auto_skill_dir, exist_ok=True)
                os.makedirs(os.path.join(auto_skill_dir, "examples"), exist_ok=True)
                
                yaml_content = {
                    "name": f"auto-{repo_slug}",
                    "description": res.get("description", f"Auto-generated conventions for {repo_slug}"),
                    "category": "Auto-Generated",
                    "tags": [repo_slug, "auto-generated"]
                }
                with open(os.path.join(auto_skill_dir, "skill.yaml"), "w", encoding="utf-8") as f:
                    yaml.safe_dump(yaml_content, f, default_flow_style=False)
                    
                with open(os.path.join(auto_skill_dir, "instructions.md"), "w", encoding="utf-8") as f:
                    f.write(res.get("instructions", "# Auto-conventions\n"))
                    
                rules_text = "\n".join(res.get("rules", ["Follow project conventions."]))
                with open(os.path.join(auto_skill_dir, "rules.txt"), "w", encoding="utf-8") as f:
                    f.write(rules_text)
                    
                click.secho(f"Success: Auto-generated engineering skill created for repository '{repo_slug}'!", fg="green", err=True)
                click.echo("You can inspect and tweak the conventions at:", err=True)
                click.echo(f"  - Rules: [rules.txt](file://{os.path.abspath(os.path.join(auto_skill_dir, 'rules.txt'))})", err=True)
                click.echo(f"  - Instructions: [instructions.md](file://{os.path.abspath(os.path.join(auto_skill_dir, 'instructions.md'))})", err=True)
            except Exception as ex:
                logger.error(f"Failed to auto-generate skill conventions: {ex}", exc_info=True)
                click.secho(f"Failed to auto-generate skill conventions: {ex}", fg="red", err=True)
