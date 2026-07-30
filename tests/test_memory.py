from kriya.memory.memory import LocalMemoryProvider, MemoryEngine


def test_memory_engine_all_scopes(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    
    provider = LocalMemoryProvider(str(memory_dir))
    engine = MemoryEngine(provider)
    
    # 1. Test Conversation Memory
    assert engine.get_conversation("session_1") == []
    
    engine.add_message("session_1", "user", "Hello platform")
    engine.add_message("session_1", "assistant", "Hello! How can I help you?")
    
    msgs = engine.get_conversation("session_1")
    assert len(msgs) == 2
    assert msgs[0] == {"role": "user", "content": "Hello platform"}
    assert msgs[1] == {"role": "assistant", "content": "Hello! How can I help you?"}
    
    engine.clear_conversation("session_1")
    assert engine.get_conversation("session_1") == []

    # 2. Test Repository Index Memory
    repo_path = "/Users/dummy/my_repo"
    assert engine.get_repository_index(repo_path) is None
    
    index_data = {"languages": {"Python": 100.0}, "frameworks": ["Django"]}
    engine.save_repository_index(repo_path, index_data)
    
    cached = engine.get_repository_index(repo_path)
    assert cached is not None
    assert cached["path"] == repo_path
    assert cached["index"] == index_data

    # 3. Test Working Memory
    assert engine.get_working_state("task_abc") == {}
    
    state = {"current_step": 3, "status": "in-progress", "subtasks": ["sub1"]}
    engine.save_working_state("task_abc", state)
    assert engine.get_working_state("task_abc") == state

    # 4. Test Preferences Memory
    assert engine.get_preferences() == {}
    
    prefs = {"theme": "dark", "auto_validate": True}
    engine.save_preferences(prefs)
    assert engine.get_preferences() == prefs
