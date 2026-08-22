import ast
import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional

from kriya.analyzer.analyzer import JAVA_METHOD_SIGNATURE_CORE
from kriya.core.db import get_connection

logger = logging.getLogger(__name__)

# Relative confidence that a relation type reflects genuine relevance for
# Graph RAG re-ranking (2026-08-12 SME review) - a direct import/inheritance
# edge is a much stronger relevance signal than a generic call or an
# annotation reference, so get_neighborhood() weights hits by this table
# (divided by hop distance) instead of treating every relation type equally.
_RELATION_WEIGHTS: Dict[str, float] = {
    "imports": 1.0,
    "inherits": 1.0,
    "implements": 1.0,
    "extends": 1.0,
    "calls": 0.7,
    "declares_bean": 0.6,
    "references_bean": 0.6,
    "annotated_with": 0.4,
    "injects": 0.4,
}
_DEFAULT_RELATION_WEIGHT = 0.5

# Used by find_java_main_class() below - covers both the classic array
# parameter and the varargs shorthand ("String... args"), which is equally
# valid for a real entrypoint.
_JAVA_MAIN_METHOD_RE = re.compile(r"public\s+static\s+void\s+main\s*\(\s*(?:final\s+)?String")
# Deliberately the SAME granularity _parse_java()'s own class_regex already
# uses elsewhere in this file (a flat, non-nesting-aware scan) - not
# requiring `public` (see find_java_main_class()'s own docstring for why).
_JAVA_TOP_LEVEL_CLASS_RE = re.compile(r"(?:public|final|abstract|\s)*\bclass\s+(\w+)")


class DependencyGraph:
    """SQLite-backed AST dependency knowledge graph compiler for multi-language repositories."""

    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create database schema if not exists."""
        self.conn = get_connection(self.db_path)
        cursor = self.conn.cursor()
        
        # Table of files and their indexing states
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                filepath TEXT PRIMARY KEY,
                mtime REAL,
                hash TEXT
            )
        """)
        try:
            cursor.execute("ALTER TABLE files ADD COLUMN hash TEXT")
        except Exception:
            pass
        
        # Table of symbols parsed (classes, methods, bean definitions)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT,
                name TEXT,
                type TEXT,
                start_line INTEGER,
                end_line INTEGER
            )
        """)
        
        # Table of relations (calls, references, imports)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                target TEXT,
                type TEXT
            )
        """)
        # source_file: which file's parse produced this relation row - added
        # after clear_file() (below) was found live to delete a DIFFERENT
        # file's relations whenever two files happen to define a
        # same-named symbol, since source/target are bare, unqualified
        # names with no file scoping. Same defensive ALTER-TABLE pattern
        # already used for files.hash above (SQLite has no "ADD COLUMN IF
        # NOT EXISTS"). Pre-existing rows from before this migration have
        # source_file=NULL - clear_file()'s fallback clause still handles
        # those with the old (imprecise) name-based match, so a real
        # re-index migrates them to the precise, file-scoped match.
        try:
            cursor.execute("ALTER TABLE relations ADD COLUMN source_file TEXT")
        except Exception:
            pass


        # Create indexes for blazing-fast lookup speeds
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbols_filepath ON symbols(filepath)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target)")
        
        self.conn.commit()

    def get_cached_mtime(self, filepath: str) -> Optional[float]:
        """Fetch cached mtime for incremental skip validation."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT mtime FROM files WHERE filepath = ?", (filepath,))
        row = cursor.fetchone()
        return row[0] if row else None

    def get_cached_hash(self, filepath: str) -> Optional[str]:
        """Fetch cached content hash for incremental skip validation."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT hash FROM files WHERE filepath = ?", (filepath,))
        row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def clear_file(self, filepath: str) -> None:
        """Delete old symbols and relationships associated with a file.

        Relations are deleted by source_file when it's populated (precise -
        only this file's own relation rows) - found live, 2026-08-12 (SME
        architecture review): the previous NAME-based match (still applied
        below as a fallback for pre-migration rows) deletes ANY relation row
        whose source/target matches a symbol name defined in this file, even
        one that actually belongs to a DIFFERENT file's same-named symbol
        (e.g. two Java files each defining a method called "handle") -
        re-indexing one file silently corrupted the other's dependency-graph
        relations, with no error surfaced."""
        cursor = self.conn.cursor()
        # Delete relations first (while symbols still exist in the database!)
        cursor.execute("""
            DELETE FROM relations
            WHERE source_file = ?
               OR (
                    source_file IS NULL
                    AND (
                        source IN (SELECT name FROM symbols WHERE filepath = ?)
                        OR target IN (SELECT name FROM symbols WHERE filepath = ?)
                    )
                  )
        """, (filepath, filepath, filepath))
        cursor.execute("DELETE FROM symbols WHERE filepath = ?", (filepath,))
        cursor.execute("DELETE FROM files WHERE filepath = ?", (filepath,))
        self.conn.commit()

    def index_file(self, rel_path: str, content: str, mtime: float, file_hash: Optional[str] = None) -> None:
        """Parse source code of a file and populate SQLite database indices."""
        if file_hash is None:
            import hashlib
            file_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()
        self.clear_file(rel_path)
        
        symbols = []
        relations = []
        
        _, ext = os.path.splitext(rel_path)
        ext = ext.lower()
        
        try:
            if ext == ".py":
                symbols, relations = self._parse_python(rel_path, content)
            elif ext == ".java":
                symbols, relations = self._parse_java(rel_path, content)
            elif ext == ".xml":
                symbols, relations = self._parse_xml(rel_path, content)
            elif ext == ".rb":
                symbols, relations = self._parse_ruby(rel_path, content)
        except Exception as e:
            logger.error(f"Error parsing dependency symbols in {rel_path}: {e}")
            return
            
        # Write to SQLite
        cursor = self.conn.cursor()
        
        cursor.execute("INSERT OR REPLACE INTO files (filepath, mtime, hash) VALUES (?, ?, ?)", (rel_path, mtime, file_hash))
        
        for sym in symbols:
            cursor.execute("""
                INSERT INTO symbols (filepath, name, type, start_line, end_line)
                VALUES (?, ?, ?, ?, ?)
            """, (rel_path, sym["name"], sym["type"], sym["start_line"], sym["end_line"]))
            
        for rel in relations:
            cursor.execute("""
                INSERT INTO relations (source, target, type, source_file)
                VALUES (?, ?, ?, ?)
            """, (rel["source"], rel["target"], rel["type"], rel_path))
            
        self.conn.commit()

    def get_callers(self, target: str) -> List[Dict[str, Any]]:
        """Retrieve calling symbols referencing the target symbol."""
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT r.source, s.filepath, s.type 
            FROM relations r
            LEFT JOIN symbols s ON r.source = s.name
            WHERE r.target = ? AND r.type = 'calls'
        """, (target,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def get_callees(self, source: str) -> List[Dict[str, Any]]:
        """Retrieve target symbols referenced or called by the source symbol."""
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT r.target, s.filepath, s.type 
            FROM relations r
            LEFT JOIN symbols s ON r.target = s.name
            WHERE r.source = ? AND r.type = 'calls'
        """, (source,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def get_imports(self, filepath: str) -> List[str]:
        """Fetch all dependency import files or packages for a specific file path."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT DISTINCT target 
            FROM relations 
            WHERE source = ? AND type = 'imports'
        """, (filepath,))
        rows = cursor.fetchall()
        return [r[0] for r in rows]

    def get_symbols_for_file(self, filepath: str) -> List[str]:
        """Fetch the real symbol names (classes/methods/functions/beans) this
        file's own parse produced - the file's actual identity in the graph,
        rather than a filename-stem guess (2026-08-12 SME review: the
        previous Graph RAG seeding used `os.path.splitext(basename(f))[0]`,
        which only happens to match a real symbol for languages/conventions
        where the public type name equals the filename)."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM symbols WHERE filepath = ?", (filepath,))
        return [r[0] for r in cursor.fetchall()]

    def get_class_symbol_locations(self) -> Dict[str, List[str]]:
        """Workspace-wide index of "ext:simple_name" (extension-scoped,
        unqualified class name) -> the distinct files that declare it, for
        kriya/workflow/attempt.py's duplicate-type-across-files Quality Gate -
        found live, 2026-08-21 (protocol_encoder_java): three separate,
        incompatible `Protocol.java` files ended up coexisting in one
        workspace (default package, `protocol`, `com.example.protocol`),
        each missing different pieces of the intended API, because nothing
        ever noticed a "new" file was actually redeclaring an existing type
        under a different path.

        Deliberately collapses on the SIMPLE name, not the qualified one
        _parse_java() stores (`package_prefix + class_name`, e.g.
        "com.example.protocol.Protocol") - a qualified-name lookup would
        never have caught the incident above, since all three files produce
        different qualified strings. The simple-name collision IS the
        signal this exists to expose, not noise to filter out (contrast
        get_symbols_for_file()'s own docstring, which wants qualified
        precision for a different purpose - seeding Graph RAG traversal).

        The key is prefixed with the declaring file's own extension
        deliberately - found live while writing this method's own test: an
        unscoped simple-name index treats Java's `Protocol` and an unrelated
        Python `Protocol` (or a JS/TS/Go one, once those languages get
        indexed) as the SAME collision, which is almost certainly wrong in
        any polyglot repo (a frontend `User` and a backend `User` are
        typically different concepts, not a duplicate). extract_class_names()
        below produces keys in this same "ext:name" format so the two never
        drift apart."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT name, filepath FROM symbols WHERE type = 'class'")
        index: Dict[str, List[str]] = {}
        for name, filepath in cursor.fetchall():
            if not name:
                continue
            simple = name.rsplit(".", 1)[-1]
            if not simple:
                continue
            ext = os.path.splitext(filepath)[1].lower()
            key = f"{ext}:{simple}"
            paths = index.setdefault(key, [])
            if filepath not in paths:
                paths.append(filepath)
        return {key: sorted(paths) for key, paths in index.items()}

    def extract_class_names(self, filepath: str, content: str) -> List[str]:
        """Public, non-persisting wrapper around this class's own per-language
        symbol parsers (the exact same dispatch index_file() uses), for a
        candidate file's content BEFORE it's ever written to disk or indexed -
        needed because RepositoryAnalyzer's re-index runs once, at the top of
        each run_generation_workflow() call, so a file written earlier in the
        SAME still-in-progress retry loop is invisible to
        get_class_symbol_locations()'s persisted baseline until a future run
        re-indexes it. Returns "ext:simple_name" keys, same extension-scoped
        convention as get_class_symbol_locations() (see that method's own
        docstring for why the extension prefix matters). Never raises - `[]`
        for any extension index_file() doesn't parse a class out of (.xml has
        no "class" concept; anything else isn't parsed at all yet) or on any
        parse error (e.g. a Python file with invalid syntax) - a failed
        extraction here must degrade to "nothing to check", never a false
        rejection of an otherwise-fine write."""
        _, ext = os.path.splitext(filepath)
        ext = ext.lower()
        try:
            if ext == ".py":
                symbols, _relations = self._parse_python(filepath, content)
            elif ext == ".java":
                symbols, _relations = self._parse_java(filepath, content)
            elif ext == ".rb":
                symbols, _relations = self._parse_ruby(filepath, content)
            else:
                return []
        except Exception as e:
            logger.debug(f"extract_class_names: could not parse {filepath}: {e}")
            return []
        names = {
            sym["name"].rsplit(".", 1)[-1]
            for sym in symbols
            if sym.get("type") == "class" and sym.get("name")
        }
        return sorted(f"{ext}:{n}" for n in names if n)

    def find_java_main_class(self, filepath: str, content: str) -> Optional[str]:
        """Deterministically detects a Java file's runnable entrypoint class -
        one with a real `public static void main(String[]...)`/`String...
        args` method - for constructing a `javac`/`java` invocation without
        ever asking an LLM to guess it. Built for the exact gap found live,
        2026-08-21 (ignite_qpid_protocol milestone 3/4): a Java project with
        no pom.xml/build.gradle has zero deterministic compile/run capability
        today (PolymorphicValidator's stack detection is Maven/Gradle-marker-
        only), so "which files to compile, what the entrypoint is" was 100%
        delegated to RunVerifierAgent.judge()'s free-form guess - three
        consecutive same-day prompt patches (established_files visibility, an
        explicit "no Maven" statement, a javac-prepend backstop) each fixed
        exactly what they targeted and surfaced the next gap underneath. This
        is the deterministic capability that removes the guess entirely for
        the common, unambiguous case.

        Deliberately conservative, matching extract_class_names()'s own
        degrade-gracefully precedent - returns None (never a wrong guess) for
        anything not confidently resolvable:
        - No `.java` extension, no real main-method signature found, or any
          parse error.
        - MORE THAN ONE top-level `class` declaration in the file - a
          multi-class file needs real scoping (which class does main()
          actually belong to) that a flat regex scan can't safely resolve;
          rather than risk attributing main() to the wrong class, this
          degrades to "no confident answer" and the caller falls back to
          today's existing (already-hardened) LLM-guess path.

        The class's own visibility is NOT required to be `public` - Java only
        requires a single top-level type per file to be public (and even
        then, only for cross-package access), never for running it via
        `java ClassName`; a package-private top-level class with a real
        main() is exactly as runnable as a public one, so requiring `public`
        here would silently miss legitimate entrypoints."""
        if not filepath.endswith(".java"):
            return None
        try:
            # Line-by-line with comment-skipping, deliberately mirroring
            # _parse_java()'s own approach above rather than a raw whole-
            # content regex scan - a bare `.search()`/`.findall()` over the
            # full text would false-positive on a comment merely DESCRIBING
            # a main method or class ("// public static void main(String[]
            # args) is required here", "// see the Foo class below") as if
            # it were real code.
            has_main = False
            class_names = []
            for line in content.splitlines():
                line_strip = line.strip()
                if line_strip.startswith("//") or line_strip.startswith("/*") or line_strip.startswith("*"):
                    continue
                if not has_main and _JAVA_MAIN_METHOD_RE.search(line_strip):
                    has_main = True
                class_match = _JAVA_TOP_LEVEL_CLASS_RE.match(line_strip)
                if class_match:
                    class_names.append(class_match.group(1))
            if not has_main or len(class_names) != 1:
                return None
            pkg_match = re.search(r"package\s+([\w.]+);", content)
            package_prefix = pkg_match.group(1) + "." if pkg_match else ""
            return package_prefix + class_names[0]
        except Exception as e:
            logger.debug(f"find_java_main_class: could not parse {filepath}: {e}")
            return None

    def get_neighborhood(self, seed_symbols: List[str], max_hops: int = 2, max_results: int = 30) -> List[Dict[str, Any]]:
        """Perform bounded BFS traversal on the symbol relationship graph.

        Each hit carries a "score" (relation-type weight / hop distance, see
        _RELATION_WEIGHTS) so callers can prioritize which related files are
        actually worth keeping when a token budget is tight, instead of
        treating every hop-2 annotation reference the same as a direct
        hop-1 import. Results are sorted by score descending, then capped
        at `max_results` - previously unbounded, so a common method name
        shared across many unrelated classes (e.g. "process", "save") could
        pull in every unrelated definition sharing that bare name as
        "related" context, since BFS matches are keyed on unqualified
        symbol names with no file/class scoping at all (2026-08-12 SME
        review). The cap applies to the final, score-sorted output, not the
        internal traversal itself - the BFS still explores as far as
        max_hops allows to find the genuinely highest-scoring hits, only
        the returned list is bounded."""
        if not seed_symbols:
            return []
            
        visited = set()
        queue = []
        for symbol in seed_symbols:
            queue.append((symbol, 0))
            visited.add(symbol)
            
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        
        results = []
        
        while queue:
            current, hop = queue.pop(0)
            if hop >= max_hops:
                continue
                
            cursor.execute("""
                SELECT r.source, r.target, r.type, s.filepath, s.type as symbol_type
                FROM relations r
                LEFT JOIN symbols s ON (r.source = s.name OR r.target = s.name)
                WHERE r.source = ? OR r.target = ?
            """, (current, current))
            
            rows = cursor.fetchall()
            for r in rows:
                source = r["source"]
                target = r["target"]
                rel_type = r["type"]
                filepath = r["filepath"]
                sym_type = r["symbol_type"]
                
                neighbor = target if source == current else source
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, hop + 1))
                    
                if filepath:
                    weight = _RELATION_WEIGHTS.get(rel_type, _DEFAULT_RELATION_WEIGHT)
                    results.append({
                        "name": neighbor,
                        "filepath": filepath,
                        "relation_type": rel_type,
                        "symbol_type": sym_type,
                        "hop": hop + 1,
                        "score": weight / (hop + 1)
                    })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:max_results]

    def close(self) -> None:
        if hasattr(self, "conn") and self.conn:
            self.conn.close()


    def _parse_python(self, filepath: str, content: str) -> tuple:
        # Deliberately does not catch parse errors here - let them propagate to
        # index_file's except block, which already logs them properly. Swallowing
        # here would make that existing error handler never fire for this case.
        symbols = []
        relations = []
        tree = ast.parse(content, filename=filepath)
        for node in ast.walk(tree):
            # 1. Capture Classes
            if isinstance(node, ast.ClassDef):
                symbols.append({
                    "name": node.name,
                    "type": "class",
                    "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno)
                })
            # 2. Capture Functions
            elif isinstance(node, ast.FunctionDef):
                symbols.append({
                    "name": node.name,
                    "type": "function",
                    "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno)
                })
            # 3. Capture Imports
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    relations.append({
                        "source": filepath,
                        "target": alias.name,
                        "type": "imports"
                    })
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    relations.append({
                        "source": filepath,
                        "target": node.module,
                        "type": "imports"
                    })

            # 4. Capture method/function calls
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    # Local call e.g. add(1, 2)
                    relations.append({
                        "source": filepath,
                        "target": node.func.id,
                        "type": "calls"
                    })
                elif isinstance(node.func, ast.Attribute):
                    # Attribute call e.g. self.evaluate()
                    relations.append({
                        "source": filepath,
                        "target": node.func.attr,
                        "type": "calls"
                    })
        return symbols, relations

    def _parse_java(self, filepath: str, content: str) -> tuple:
        symbols = []
        relations = []
        lines = content.splitlines()
        
        # Package name
        pkg_match = re.search(r"package\s+([\w\.]+);", content)
        package_prefix = pkg_match.group(1) + "." if pkg_match else ""
        
        # Regex mappings for Java classes, methods and fields
        class_regex = re.compile(
            r"(?:public|protected|private|static|\s)*class\s+(\w+)"
            r"(?:\s+extends\s+(\w+))?"
            r"(?:\s+implements\s+([\w\s,]+))?"
        )
        method_regex = re.compile(JAVA_METHOD_SIGNATURE_CORE + r"\s*\{?")
        import_regex = re.compile(r"import\s+([\w\.\*]+);")
        field_regex = re.compile(r"(?:public|protected|private|static|final|\s)*([\w<>\?]+)\s+(\w+)\s*;")
        
        pending_annotations = []
        
        for idx, line in enumerate(lines, 1):
            line_strip = line.strip()
            
            # Skip comments
            if line_strip.startswith("//") or line_strip.startswith("/*") or line_strip.startswith("*"):
                continue
                
            # Capture annotations
            anno_matches = re.findall(r"@(\w+)", line_strip)
            if anno_matches:
                pending_annotations.extend(anno_matches)
            
            # Capture imports
            imp_match = import_regex.match(line_strip)
            if imp_match:
                relations.append({
                    "source": filepath,
                    "target": imp_match.group(1),
                    "type": "imports"
                })
                continue
                
            # Class definitions
            class_match = class_regex.match(line_strip)
            if class_match:
                class_name = package_prefix + class_match.group(1)
                symbols.append({
                    "name": class_name,
                    "type": "class",
                    "start_line": idx,
                    "end_line": idx + 5
                })
                
                # Add class annotation relations
                for anno in pending_annotations:
                    relations.append({
                        "source": class_name,
                        "target": anno,
                        "type": "annotated_with"
                    })
                pending_annotations = []
                
                # Handle extends
                base_class = class_match.group(2)
                if base_class:
                    relations.append({
                        "source": class_name,
                        "target": base_class,
                        "type": "inherits"
                    })
                    
                # Handle implements
                impl_interfaces = class_match.group(3)
                if impl_interfaces:
                    for interface in impl_interfaces.split(","):
                        interface = interface.strip()
                        if interface:
                            relations.append({
                                "source": class_name,
                                "target": interface,
                                "type": "implements"
                            })
                continue
                
            # Field DI injection matching
            field_match = field_regex.match(line_strip)
            if field_match:
                field_type = field_match.group(1)
                if field_type not in {"String", "int", "long", "double", "float", "boolean", "char", "byte", "short"}:
                    if any(a in pending_annotations for a in {"Autowired", "Resource", "Inject", "Qualifier"}):
                        relations.append({
                            "source": filepath,
                            "target": field_type,
                            "type": "injects"
                        })
                pending_annotations = []
                
            # Method definitions
            method_match = method_regex.match(line_strip)
            if method_match:
                method_name = method_match.group(1)
                # Ignore java keywords
                if method_name not in {"if", "for", "while", "switch", "catch"}:
                    symbols.append({
                        "name": method_name,
                        "type": "method",
                        "start_line": idx,
                        "end_line": idx + 2
                    })
                    
            # Capture method invocations
            calls = re.findall(r"\.(\w+)\(", line_strip)
            for call in calls:
                if call not in {"println", "print", "equals", "toString", "split", "replace"}:
                    relations.append({
                        "source": filepath,
                        "target": call,
                        "type": "calls"
                    })
                    
        return symbols, relations

    def _parse_xml(self, filepath: str, content: str) -> tuple:
        symbols = []
        relations = []
        
        import xml.etree.ElementTree as ET
        try:
            # Strip XML namespace tags to make XPath lookup robust and uniform
            cleaned_content = re.sub(r'\sxmlns="[^"]+"', '', content)
            cleaned_content = re.sub(r'\sxmlns:[^=]+="[^"]+"', '', cleaned_content)
            cleaned_content = re.sub(r'<beans[^>]*>', '<beans>', cleaned_content)
            
            root = ET.fromstring(cleaned_content)
            for bean in root.findall(".//bean"):
                bean_id = bean.get("id") or bean.get("name")
                bean_class = bean.get("class")
                if not bean_id:
                    continue
                    
                symbols.append({
                    "name": bean_id,
                    "type": "spring_bean",
                    "start_line": 1,
                    "end_line": 1
                })
                
                if bean_class:
                    relations.append({
                        "source": bean_id,
                        "target": bean_class,
                        "type": "declares_bean"
                    })
                    
                # Property references
                for prop in bean.findall(".//property"):
                    prop_ref = prop.get("ref")
                    if prop_ref:
                        relations.append({
                            "source": bean_id,
                            "target": prop_ref,
                            "type": "references_bean"
                        })
                        
                # Constructor arguments
                for carg in bean.findall(".//constructor-arg"):
                    carg_ref = carg.get("ref")
                    if carg_ref:
                        relations.append({
                            "source": bean_id,
                            "target": carg_ref,
                            "type": "references_bean"
                        })
        except Exception as e:
            logger.warning(f"ElementTree XML parse failed for {filepath}: {e}, falling back to regex")
            bean_regex = re.compile(r'<bean\s+(?:id|name)="([^"]+)"(?:\s+class="([^"]+)")?')
            for idx, line in enumerate(content.splitlines(), 1):
                match = bean_regex.search(line)
                if match:
                    bean_id = match.group(1)
                    bean_class = match.group(2) or ""
                    symbols.append({
                        "name": bean_id,
                        "type": "spring_bean",
                        "start_line": idx,
                        "end_line": idx
                    })
                    if bean_class:
                        relations.append({
                            "source": bean_id,
                            "target": bean_class,
                            "type": "declares_bean"
                        })
                        
        return symbols, relations

    def _parse_ruby(self, filepath: str, content: str) -> tuple:
        symbols = []
        relations = []
        lines = content.splitlines()
        
        class_regex = re.compile(r"class\s+([\w\:]+)")
        def_regex = re.compile(r"def\s+([\w\?\!\=]+)")
        require_regex = re.compile(r"(?:require|require_relative)\s+['\"]([^'\"]+)['\"]")
        
        for idx, line in enumerate(lines, 1):
            line_strip = line.strip()
            
            # Requires
            req_match = require_regex.match(line_strip)
            if req_match:
                relations.append({
                    "source": filepath,
                    "target": req_match.group(1),
                    "type": "imports"
                })
                continue
                
            # Class definitions
            class_match = class_regex.match(line_strip)
            if class_match:
                symbols.append({
                    "name": class_match.group(1),
                    "type": "class",
                    "start_line": idx,
                    "end_line": idx
                })
                continue
                
            # Method definitions
            def_match = def_regex.match(line_strip)
            if def_match:
                symbols.append({
                    "name": def_match.group(1),
                    "type": "method",
                    "start_line": idx,
                    "end_line": idx
                })
                
            # Simple call matching e.g. service.calculate(x)
            calls = re.findall(r"\.(\w+)[\s\(]", line_strip)
            for call in calls:
                if call not in {"new", "each", "map", "puts", "to_s", "nil?", "include?"}:
                    relations.append({
                        "source": filepath,
                        "target": call,
                        "type": "calls"
                    })
                    
        return symbols, relations
