import ast
import json
import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional

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

_JAVA_PRIMITIVE_FIELD_TYPES = frozenset({"String", "int", "long", "double", "float", "boolean", "char", "byte", "short"})
_JAVA_INJECTION_ANNOTATIONS = frozenset({"Autowired", "Resource", "Inject", "Qualifier"})
_JAVA_IGNORED_CALLS = frozenset({"println", "print", "equals", "toString", "split", "replace"})
# Bump when the symbols/relations this graph stores change shape or meaning.
STRUCTURAL_INDEX_SCHEMA_VERSION = "kriya-structural-index/1"


def structural_identity() -> Dict[str, str]:
    """Everything that decides what this graph stores for the same bytes."""
    from kriya.code_intel.parsing import parser_identity

    parser = parser_identity()
    return {
        "schema_version": STRUCTURAL_INDEX_SCHEMA_VERSION, "tree_sitter": parser.tree_sitter,
        "java_grammar": parser.java_grammar, "python_grammar": parser.python_grammar,
        "structural_parser": parser.structural_parser,
    }


# Top-level type declarations (a nested type is stored as nested_<kind>).
_TOP_LEVEL_TYPE_SYMBOLS = ("class", "interface", "enum", "record", "annotation_type")

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
        # PRV-11 (2026-08-31): ":memory:" is sqlite3's own special connection
        # string for a private, ephemeral, in-process database - never a
        # real filesystem path. os.path.abspath(":memory:") does NOT
        # preserve it (it resolves to a literal path ending in "/:memory:"),
        # and the os.makedirs() below would then create real directories
        # and, on connect, a real on-disk file with that exact name - found
        # live: a bounded, in-memory-only planning-evidence graph (kriya/
        # workflow/workflow_controller.py's build_planning_structural_
        # evidence()) ended up creating and committing a real ":memory:"
        # file into the generated workspace. Preserved verbatim, never
        # touched by abspath/makedirs, for this one literal value only -
        # every real filesystem path is completely unaffected.
        if db_path == ":memory:":
            self.db_path = db_path
        else:
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
        # CTX-001 P0 (S1/S5 probes) found clear_file()'s own
        # "DELETE FROM relations WHERE source_file = ?" doing a full table
        # scan on every single file re-index, because source_file (added
        # above via ALTER TABLE, after the table already existed) never got
        # an index of its own - superimposing an extra O(N) scan onto every
        # file indexed, i.e. O(N^2) total for a full cold index. This is the
        # single highest-leverage fix P0 identified (root-caused via
        # EXPLAIN QUERY PLAN). CREATE INDEX IF NOT EXISTS is itself safe to
        # run against a pre-existing database that already has rows (and
        # possibly NULL source_file values from before the ALTER TABLE
        # above) - SQLite indexes NULL values like any other value, and
        # clear_file()'s own OR-fallback clause for NULL rows is unaffected
        # by whether source_file is indexed.
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_source_file ON relations(source_file)")
        # Code Intelligence R1: the raw-byte sha256 each file's structure was
        # parsed from, and the identity of the parser that produced it.
        try:
            cursor.execute("ALTER TABLE files ADD COLUMN source_digest TEXT")
        except Exception:
            pass
        cursor.execute("CREATE TABLE IF NOT EXISTS index_manifest (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

        self.conn.commit()

    def manifest(self) -> Dict[str, str]:
        return dict(self.conn.execute("SELECT key, value FROM index_manifest").fetchall())

    def adopt_structural_identity(self, embedding_fingerprint: Optional[str] = None) -> bool:
        """Bind this graph to the current structural identity (schema +
        tree-sitter + grammar + structural parser versions). Structure built
        under any other identity - including a pre-R1 graph with no manifest
        at all - is never reused: every file's symbols/relations are dropped
        so the next pass re-parses them. Returns True when that happened."""
        identity = structural_identity()
        stored = self.manifest().get("structural_identity")
        with self.conn:
            stale = stored != json.dumps(identity, sort_keys=True) and self.has_indexed_files()
            if stale:
                for table in ("relations", "symbols", "files"):
                    self.conn.execute(f"DELETE FROM {table}")
            self.conn.execute("INSERT OR REPLACE INTO index_manifest (key, value) VALUES (?, ?)",
                              ("structural_identity", json.dumps(identity, sort_keys=True)))
            if embedding_fingerprint is not None:
                self.conn.execute("INSERT OR REPLACE INTO index_manifest (key, value) VALUES (?, ?)",
                                  ("embedding_fingerprint", embedding_fingerprint))
        return stale

    def record_manifest(self, **values: str) -> None:
        with self.conn:
            for key, value in values.items():
                self.conn.execute("INSERT OR REPLACE INTO index_manifest (key, value) VALUES (?, ?)", (key, value))

    def has_indexed_files(self) -> bool:
        """Cheap existence check (a single-row LIMIT 1, not a COUNT scan) - True
        once index_repository() has recorded at least one file for this
        workspace's dependency_graph.db, regardless of how many. Used by
        run_generation_workflow()'s autonomy.auto_index_missing_dependency_graph
        gate to decide whether a workspace has ever been indexed at all -
        see that config field's own docstring (kriya/config/config.py) for
        why this needs to be cheap: it runs on every call, including every
        milestone in a decomposed sequence, and must stay a no-op once the
        real one-time index has already happened."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM files LIMIT 1")
        return cursor.fetchone() is not None

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

    def indexed_paths(self) -> set:
        """Every file this graph holds symbols or file-sourced relations for."""
        rows = self.conn.execute(
            "SELECT filepath FROM files UNION SELECT filepath FROM symbols"
            " UNION SELECT source_file FROM relations WHERE source_file IS NOT NULL").fetchall()
        return {row[0] for row in rows}

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

    def index_file(self, rel_path: str, content: str, mtime: float, file_hash: Optional[str] = None,
                   source_digest: Optional[str] = None) -> None:
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
        
        cursor.execute("INSERT OR REPLACE INTO files (filepath, mtime, hash, source_digest) VALUES (?, ?, ?, ?)",
                       (rel_path, mtime, file_hash, source_digest))
        
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

    def find_symbol_locations(self, name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """DEV-INV-001: exact, indexed point lookup on the `symbols` table's
        own `idx_symbols_name` index - name -> every {filepath, type,
        start_line, end_line} this repository's own parse produced for that
        exact string. Backs the `find_symbol` investigation verb.

        Deliberately an EXACT match only, never a LIKE/substring scan - a
        symbol name search must stay a cheap, indexed point lookup regardless
        of repository size (see this module's own performance discipline:
        get_callers/get_callees already only ever do exact-match lookups
        against idx_relations_source/idx_relations_target). _parse_java()
        stores QUALIFIED names (package_prefix + class name) for class-level
        symbols, so an exact match on a bare simple name will legitimately
        find nothing for those - this is an honest MVP limitation (the
        caller should be told to try the fully-qualified name), never
        silently widened into a `LIKE '%.' || ? ` scan, which would defeat
        the whole point of an indexed lookup."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT filepath, name, type, start_line, end_line FROM symbols "
            "WHERE name = ? LIMIT ?",
            (name, limit),
        )
        return [
            {"filepath": r[0], "name": r[1], "type": r[2], "start_line": r[3], "end_line": r[4]}
            for r in cursor.fetchall()
        ]

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
        drift apart.

        type IN ('class', 'interface') (2026-09-07, P7 preflight): an
        interface declaration is exactly as real a type symbol for this
        index's own purpose (a duplicate/collision hazard, and a resolvable
        target for a cross-file reference) as a class - see _parse_java()'s
        own docstring comment at its class_regex definition for the live
        incident this closes."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT DISTINCT name, filepath FROM symbols WHERE type IN (%s)" % ",".join("?" * len(_TOP_LEVEL_TYPE_SYMBOLS)),
            _TOP_LEVEL_TYPE_SYMBOLS,
        )
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
            if sym.get("type") in _TOP_LEVEL_TYPE_SYMBOLS and sym.get("name")
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
                SELECT r.source, r.target, r.type, r.source_file, s.filepath, s.type as symbol_type
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
                weight = _RELATION_WEIGHTS.get(rel_type, _DEFAULT_RELATION_WEIGHT)
                
                neighbor = target if source == current else source
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, hop + 1))
                    
                if filepath:
                    results.append({
                        "name": neighbor,
                        "filepath": filepath,
                        "relation_type": rel_type,
                        "symbol_type": sym_type,
                        "hop": hop + 1,
                        "score": weight / (hop + 1)
                    })
                    # PRD-027: walk the defining file's own file-sourced
                    # relations (its calls/imports) on the next hop - the
                    # only way a two-hop dependency is ever reached.
                    if filepath not in visited:
                        visited.add(filepath)
                        queue.append((filepath, hop + 1))
                # PRD-027: calls/imports are recorded with the calling FILE as
                # their source (both parsers), and the symbols join above can
                # only ever report a DEFINER's file - so a caller was never a
                # neighbor. The relation's own source file is that caller.
                if r["source_file"] and neighbor == r["source_file"]:
                    results.append({
                        "name": neighbor,
                        "filepath": neighbor,
                        "relation_type": rel_type,
                        "symbol_type": "file",
                        "hop": hop + 1,
                        "score": weight / (hop + 1)
                    })

        # PRD-027: one entry per file (its best-scoring hit) BEFORE the cap.
        # The symbol join emits a row per matching symbol, so capping the raw
        # rows let duplicates of one file crowd distinct files out entirely
        # (a real one-hop dependency was lost this way). Ties break on path
        # for a deterministic order.
        best_by_file: Dict[str, Dict[str, Any]] = {}
        for hit in results:
            known = best_by_file.get(hit["filepath"])
            if known is None or hit["score"] > known["score"]:
                best_by_file[hit["filepath"]] = hit
        ranked = sorted(best_by_file.values(), key=lambda hit: (-hit["score"], hit["filepath"]))
        return ranked[:max_results]

    def close(self) -> None:
        if hasattr(self, "conn") and self.conn:
            self.conn.close()


    @staticmethod
    def _python_base_name(node: ast.expr) -> Optional[str]:
        """Best-effort deterministic dotted name for a class base expression,
        for CTX-001 P1 WP2 (Python inheritance relations). Only resolves
        statically nameable forms:
          - `Base` (ast.Name)
          - `pkg.Base` / `pkg.sub.Base` (ast.Attribute chain rooted in a Name)
        Returns None for anything else (a call - `get_base()`, a subscript -
        `Generic[T]`, a conditional expression, ...) - deliberately
        conservative, mirroring extract_class_names()'s own established
        "degrade gracefully, never fabricate" precedent: an unsupported/
        dynamic base must never invent a relationship (CTX-001 P1 WP2
        requirement)."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = DependencyGraph._python_base_name(node.value)
            if prefix is None:
                return None
            return f"{prefix}.{node.attr}"
        return None

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
                # CTX-001 P1 WP2: inheritance relations, source-keyed by the
                # class's own (bare) name - the same convention _parse_java()
                # already established for its "inherits"/"implements"
                # relations (source=class_name, target=base/interface name),
                # so a class's base is a genuine Graph RAG neighbor exactly
                # like a Java subclass's own superclass/interface already is.
                # Only ast.ClassDef.bases is walked - metaclass=/keyword
                # bases are never a base class and are correctly ignored by
                # only iterating node.bases. No runtime import resolution,
                # no general type inference: a base that isn't statically
                # nameable (_python_base_name returns None) produces no
                # relation at all, never a guessed one.
                for base in node.bases:
                    base_name = self._python_base_name(base)
                    if base_name:
                        relations.append({
                            "source": node.name,
                            "target": base_name,
                            "type": "inherits",
                        })
            # 2. Capture Functions
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
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
        """Symbols and relations from the Code Intelligence structural model
        (tree-sitter; E-02): every type (class, interface, enum, record,
        annotation type, nested types as ``pkg.Outer.Inner``), constructor and
        method with its real declaration span. Relations keep their
        established shapes: file-sourced ``imports``/``calls``/``injects``,
        type-sourced ``inherits``/``implements``/``annotated_with``. A
        supertype written as a simple name is resolved lexically (an exact
        single-type import, else the file's own package), so the edge joins
        the declaring type's qualified symbol; that is a structural guess
        about a name, never a type-resolved fact."""
        from kriya.code_intel.model import ParseState
        from kriya.code_intel.parsing import parse_text

        structure = parse_text(filepath, content)
        if structure.state is ParseState.PARSE_FAILED:
            raise ValueError(structure.detail)
        imports = [i for i in structure.imports if not i.startswith("static ")]
        by_simple_import = {i.rsplit(".", 1)[-1]: i for i in imports if not i.endswith("*")}

        def qualify(name: str) -> str:
            if "." in name:
                return name
            if name in by_simple_import:
                return by_simple_import[name]
            return f"{structure.namespace}.{name}" if structure.namespace else name

        symbols = []
        relations = [{"source": filepath, "target": i, "type": "imports"} for i in imports]
        for symbol in structure.symbols:
            span = {"start_line": symbol.declaration.start_line, "end_line": symbol.declaration.end_line}
            if symbol.is_type:
                symbols.append({"name": symbol.lookup_key, "type": symbol.kind if symbol.parent_id is None
                                else f"nested_{symbol.kind}", **span})
                for base in symbol.extends:
                    relations.append({"source": symbol.lookup_key, "target": qualify(base), "type": "inherits"})
                for interface in symbol.implements:
                    relations.append({"source": symbol.lookup_key, "target": qualify(interface),
                                      "type": "implements"})
                for annotation in symbol.annotations:
                    relations.append({"source": symbol.lookup_key, "target": annotation.rsplit(".", 1)[-1],
                                      "type": "annotated_with"})
            elif symbol.kind in ("method", "constructor"):
                symbols.append({"name": symbol.name, "type": symbol.kind, **span})
            elif symbol.kind == "field" and symbol.return_type and symbol.return_type not in _JAVA_PRIMITIVE_FIELD_TYPES:
                if any(a.rsplit(".", 1)[-1] in _JAVA_INJECTION_ANNOTATIONS for a in symbol.annotations):
                    relations.append({"source": filepath, "target": symbol.return_type.replace(" ", ""),
                                      "type": "injects"})
        relations.extend({"source": filepath, "target": call, "type": "calls"}
                         for call in structure.calls if call not in _JAVA_IGNORED_CALLS)
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
