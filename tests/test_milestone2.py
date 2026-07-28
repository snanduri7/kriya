import pytest
import os
import shutil
import sqlite3
from kriya.memory.vector import LocalVectorStore
from kriya.analyzer.graph import DependencyGraph
from kriya.workflow.workflow import skeletonize_code

def test_hybrid_retrieval(tmp_path):
    db_file = tmp_path / "vector.db"
    store = LocalVectorStore(str(db_file))
    
    # Enable FTS
    store.use_fts = True
    store.add_document(
        filepath="Service.java",
        text="public class UserService { public void save() {} }",
        embedding=[0.1] * 768,
        chunk_index=0,
        model_name="default",
        dimensions=768
    )
    
    # 1. Lexical matching query
    lex_res = store.query_lexical("UserService", top_k=5)
    assert len(lex_res) == 1
    assert lex_res[0]["filepath"] == "Service.java"
    
    # 2. Hybrid search query using RRF
    hybrid_res = store.query_hybrid(
        query_text="UserService",
        query_embedding=[0.1] * 768,
        top_k=5,
        model_name="default",
        dimensions=768
    )
    assert len(hybrid_res) == 1
    assert hybrid_res[0]["filepath"] == "Service.java"
    assert "score" in hybrid_res[0]

def test_spring_xml_parser(tmp_path):
    db_file = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_file))
    
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <beans>
        <bean id="igniteServer" class="org.apache.ignite.Ignite">
            <property name="configuration" ref="igniteCfg" />
        </bean>
        <bean id="igniteCfg" class="org.apache.ignite.configuration.IgniteConfiguration" />
    </beans>
    """
    
    graph.index_file("spring.xml", xml_content, 123.0)
    
    # Verify declared beans
    conn = sqlite3.connect(graph.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT source, target, type FROM relations WHERE source = 'igniteServer'")
    rows = cursor.fetchall()
    conn.close()
    
    types = {r[2] for r in rows}
    assert "declares_bean" in types
    assert "references_bean" in types

def test_context_skeletonization():
    java_code = """package com.example;
    public class SimpleClass {
        private String name;
        public void process() {
            System.out.println("Processing");
            doStuff();
        }
    }"""
    
    # Skeleton tier: Elides braced method bodies
    skel = skeletonize_code(java_code, "SimpleClass.java", "skeleton")
    assert "System.out.println" not in skel
    assert "process()" in skel
    assert "..." in skel
    
    # Signatures tier: Keep only class header / packages / imports
    sigs = skeletonize_code(java_code, "SimpleClass.java", "signatures")
    assert "process()" not in sigs
    assert "class SimpleClass" in sigs
    
    py_code = """import os
class Processor:
    def handle(self):
        x = 1
        y = 2
"""
    skel_py = skeletonize_code(py_code, "proc.py", "skeleton")
    assert "x = 1" not in skel_py
    assert "handle(self)" in skel_py
    assert "..." in skel_py
