import os
import re
import sqlite3
import logging
import ast
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class DependencyGraph:
    """SQLite-backed AST dependency knowledge graph compiler for multi-language repositories."""

    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create database schema if not exists."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Table of files and their indexing states
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                filepath TEXT PRIMARY KEY,
                mtime REAL
            )
        """)
        
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
        
        # Create indexes for blazing-fast lookup speeds
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbols_filepath ON symbols(filepath)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target)")
        
        conn.commit()
        conn.close()

    def get_cached_mtime(self, filepath: str) -> Optional[float]:
        """Fetch cached mtime for incremental skip validation."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT mtime FROM files WHERE filepath = ?", (filepath,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    def clear_file(self, filepath: str) -> None:
        """Delete old symbols and relationships associated with a file."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM files WHERE filepath = ?", (filepath,))
        cursor.execute("DELETE FROM symbols WHERE filepath = ?", (filepath,))
        # Delete any relation whose source or target references a local symbol in this file
        cursor.execute("""
            DELETE FROM relations 
            WHERE source IN (SELECT name FROM symbols WHERE filepath = ?)
               OR target IN (SELECT name FROM symbols WHERE filepath = ?)
        """, (filepath, filepath))
        conn.commit()
        conn.close()

    def index_file(self, rel_path: str, content: str, mtime: float) -> None:
        """Parse source code of a file and populate SQLite database indices."""
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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("INSERT OR REPLACE INTO files (filepath, mtime) VALUES (?, ?)", (rel_path, mtime))
        
        for sym in symbols:
            cursor.execute("""
                INSERT INTO symbols (filepath, name, type, start_line, end_line)
                VALUES (?, ?, ?, ?, ?)
            """, (rel_path, sym["name"], sym["type"], sym["start_line"], sym["end_line"]))
            
        for rel in relations:
            cursor.execute("""
                INSERT INTO relations (source, target, type)
                VALUES (?, ?, ?)
            """, (rel["source"], rel["target"], rel["type"]))
            
        conn.commit()
        conn.close()

    def get_callers(self, target: str) -> List[Dict[str, Any]]:
        """Retrieve calling symbols referencing the target symbol."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.source, s.filepath, s.type 
            FROM relations r
            LEFT JOIN symbols s ON r.source = s.name
            WHERE r.target = ? AND r.type = 'calls'
        """, (target,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_callees(self, source: str) -> List[Dict[str, Any]]:
        """Retrieve target symbols referenced or called by the source symbol."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.target, s.filepath, s.type 
            FROM relations r
            LEFT JOIN symbols s ON r.target = s.name
            WHERE r.source = ? AND r.type = 'calls'
        """, (source,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_imports(self, filepath: str) -> List[str]:
        """Fetch all dependency import files or packages for a specific file path."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT target 
            FROM relations 
            WHERE source = ? AND type = 'imports'
        """, (filepath,))
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]

    # =====================================================================
    # Language AST Parsers
    # =====================================================================

    def _parse_python(self, filepath: str, content: str) -> tuple:
        symbols = []
        relations = []
        try:
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
        except Exception:
            pass
        return symbols, relations

    def _parse_java(self, filepath: str, content: str) -> tuple:
        symbols = []
        relations = []
        lines = content.splitlines()
        
        # 1. Package name
        pkg_match = re.search(r"package\s+([\w\.]+);", content)
        package_prefix = pkg_match.group(1) + "." if pkg_match else ""
        
        # Regex mappings for Java classes and functions
        class_regex = re.compile(r"(?:public|protected|private|static|\s)*class\s+(\w+)")
        method_regex = re.compile(r"(?:public|protected|private|static|\s)+[\w<>]+\s+(\w+)\s*\([^\)]*\)\s*\{?")
        import_regex = re.compile(r"import\s+([\w\.\*]+);")
        
        for idx, line in enumerate(lines, 1):
            line_strip = line.strip()
            
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
                    "end_line": idx + 5 # baseline placeholder
                })
                continue
                
            # Method definitions
            method_match = method_regex.match(line_strip)
            if method_match:
                method_name = method_match.group(1)
                # Ignore java keywords e.g. if, for, while
                if method_name not in {"if", "for", "while", "switch", "catch"}:
                    symbols.append({
                        "name": method_name,
                        "type": "method",
                        "start_line": idx,
                        "end_line": idx + 2
                    })
                    
            # Capture method invocations (regex lookup for method calls e.g. service.saveUser())
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
        
        # Spring XML Bean definitions e.g. <bean id="userController" class="com.kriya.UserController" />
        bean_regex = re.compile(r'<bean\s+id="([^"]+)"\s+class="([^"]+)"')
        for idx, line in enumerate(content.splitlines(), 1):
            match = bean_regex.search(line)
            if match:
                bean_id = match.group(1)
                bean_class = match.group(2)
                symbols.append({
                    "name": bean_id,
                    "type": "spring_bean",
                    "start_line": idx,
                    "end_line": idx
                })
                relations.append({
                    "source": bean_id,
                    "target": bean_class,
                    "type": "references"
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
