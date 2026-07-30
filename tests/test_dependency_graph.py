from kriya.analyzer.graph import DependencyGraph


def test_dependency_graph_indexing(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    
    # 1. Test Python parsing and SQL insertion
    py_content = """
import sys
from kriya.core import Kernel

class MyEngine:
    def execute(self):
        self.evaluate()
        run_process()
"""
    graph.index_file("my_engine.py", py_content, 100.0)
    
    # Assert symbol entries
    conn = graph.db_path
    import sqlite3
    c = sqlite3.connect(conn)
    cursor = c.cursor()
    
    cursor.execute("SELECT name, type FROM symbols WHERE filepath = 'my_engine.py'")
    syms = cursor.fetchall()
    assert ("MyEngine", "class") in syms
    assert ("execute", "function") in syms
    
    # Check imports relation
    imports = graph.get_imports("my_engine.py")
    assert "sys" in imports
    assert "kriya.core" in imports
    
    # Check calls relation
    callers = graph.get_callers("evaluate")
    assert len(callers) > 0
    assert callers[0]["source"] == "my_engine.py"
    
    c.close()

def test_java_conventions_indexing(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    
    java_content = """
package com.example.service;
import com.example.model.User;

public class UserService {
    public void saveUser(User u) {
        userRepository.save(u);
    }
}
"""
    graph.index_file("UserService.java", java_content, 200.0)
    
    # Verify symbols
    import sqlite3
    c = sqlite3.connect(graph.db_path)
    cursor = c.cursor()
    cursor.execute("SELECT name FROM symbols WHERE type = 'class'")
    classes = [r[0] for r in cursor.fetchall()]
    assert "com.example.service.UserService" in classes
    
    # Verify save method reference call relation
    callers = graph.get_callers("save")
    assert len(callers) > 0
    assert callers[0]["source"] == "UserService.java"
    c.close()

def test_ruby_conventions_indexing(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    
    ruby_content = """
require 'rspec'
require_relative 'helper'

class OrderProcessor
  def calculate_tax(order)
    order.compute_total()
  end
end
"""
    graph.index_file("order_processor.rb", ruby_content, 300.0)
    
    # Assert imports
    imports = graph.get_imports("order_processor.rb")
    assert "rspec" in imports
    assert "helper" in imports
    
    # Assert calls
    callers = graph.get_callers("compute_total")
    assert len(callers) > 0
    assert callers[0]["source"] == "order_processor.rb"

def test_xml_bean_indexing(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    
    xml_content = """
<?xml version="1.0" encoding="UTF-8"?>
<beans>
    <bean id="myService" class="com.example.MyService" />
</beans>
"""
    graph.index_file("beans.xml", xml_content, 400.0)
    
    import sqlite3
    c = sqlite3.connect(graph.db_path)
    cursor = c.cursor()
    cursor.execute("SELECT name, type FROM symbols WHERE filepath = 'beans.xml'")
    syms = cursor.fetchall()
    assert ("myService", "spring_bean") in syms
    c.close()
