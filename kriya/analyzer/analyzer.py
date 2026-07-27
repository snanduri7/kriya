import os
import re
import json
import logging
from typing import Dict, List, Any, Optional, Callable
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
            ".pytest_cache", "build", "dist", ".egg-info", "eggs", "bin", "obj"
        }

        for root, dirs, files in os.walk(self.root_path):
            # Modify dirs in-place to skip ignored directories
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
            
            rel_path = os.path.relpath(root, self.root_path)
            if rel_path != ".":
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
        setup_path = os.path.join(self.root_path, "setup.py")

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
            except Exception:
                pass

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
            except Exception:
                pass

        # 3. Parse package.json
        if os.path.exists(package_json_path):
            frameworks.add("Node.js")
            try:
                with open(package_json_path, "r", errors="replace") as f:
                    data = json.load(f)
                    all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                    for dep in all_deps.keys():
                        dependencies.add(dep)
            except Exception:
                pass

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
            except Exception:
                pass

        if os.path.exists(gradle_path):
            frameworks.add("Gradle (Java)")
            try:
                with open(gradle_path, "r", errors="replace") as f:
                    content = f.read()
                    if "spring-boot" in content:
                        frameworks.add("Spring Boot")
                    if "junit" in content:
                        testing.add("JUnit")
            except Exception:
                pass

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
            except Exception:
                pass

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
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> None:
        """Walks the repository, chunks code files, generates semantic embeddings, and stores them in LocalVectorStore."""
        from kriya.memory.vector import OllamaEmbeddingClient, LocalVectorStore
        
        # 1. Resolve storage paths
        vector_index_path = os.path.join(cfg.paths.memory, "vector_index.json")
        store = LocalVectorStore(vector_index_path)
        client = OllamaEmbeddingClient(base_url=cfg.embedding.base_url, model=cfg.embedding.model)
        
        # 2. Find target files
        target_extensions = {".py", ".java", ".xml"}
        ignore_dirs = {
            ".git", ".venv", "venv", "node_modules", "__pycache__", 
            ".pytest_cache", "build", "dist", ".egg-info", "eggs", "bin", "obj"
        }
        
        files_to_index = []
        for root, dirs, files in os.walk(self.root_path):
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
            for file in files:
                if file.startswith("."):
                    continue
                _, ext = os.path.splitext(file)
                if ext.lower() in target_extensions:
                    files_to_index.append(os.path.join(root, file))
                    
        total_files = len(files_to_index)
        logger.info(f"Discovered {total_files} files for semantic indexing.")
        
        # 3. Index files incrementally
        current_rel_paths = set()
        for idx, filepath in enumerate(files_to_index, 1):
            rel_path = os.path.relpath(filepath, self.root_path)
            current_rel_paths.add(rel_path)
            
            try:
                # Check modified time (mtime)
                mtime = os.path.getmtime(filepath)
                cached_mtime = store.file_metadata.get(rel_path, {}).get("mtime")
                
                if cached_mtime == mtime:
                    if progress_callback:
                        progress_callback(f"{rel_path} [Up-to-date]", idx, total_files)
                    continue

                if progress_callback:
                    progress_callback(rel_path, idx, total_files)

                # Clear old chunks first to support re-indexing clean
                store.remove_file(rel_path)
                
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                
                # Chunk file syntactically with overlap (increased size to 120 lines to reduce call counts)
                chunks = chunk_file_syntactically(content, max_lines=120, overlap=15)
                
                chunk_texts = [c["text"] for c in chunks if c["text"].strip()]
                if chunk_texts:
                    # Generate all embeddings concurrently
                    embs = await client.get_embeddings(chunk_texts)
                    
                    for chunk_idx, (chunk_text, emb) in enumerate(zip(chunk_texts, embs)):
                        store.add_document(
                            filepath=rel_path,
                            text=chunk_text,
                            embedding=emb,
                            chunk_index=chunk_idx
                        )
                # Store new mtime cache metadata
                store.file_metadata[rel_path] = {"mtime": mtime}
            except Exception as e:
                logger.error(f"Failed to index file {rel_path}: {e}")
                
        # Remove deleted files from cached index
        cached_files = list(store.file_metadata.keys())
        for cached_file in cached_files:
            if cached_file not in current_rel_paths:
                logger.info(f"Removing deleted file from index cache: {cached_file}")
                store.remove_file(cached_file)
                
        # 4. Save persistent cache index
        store.save()
        logger.info("Semantic repository indexing completed.")
