"""User-run PRD-003 JSON contract smoke against a real local Ollama runtime."""

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import yaml
from _live_process import run_reporting_timeout
from _live_smoke_contract import (
    SMOKE_AUTONOMY_BOUNDS,
    SMOKE_LLM_BOUNDS,
    SMOKE_PROCESS_LIMIT_SECONDS,
    assert_smoke_contract,
    workspace_snapshot,
)

from kriya.core.llm import is_local_url

pytestmark = pytest.mark.live_model


def test_real_generate_emits_one_json_result(tmp_path):
    endpoint = os.environ.get('KRIYA_LIVE_BASE_URL', 'http://localhost:11434/v1').rstrip('/')
    model = os.environ.get('KRIYA_LIVE_LLM_MODEL', 'qwen2.5-coder:1.5b')
    embedding = os.environ.get('KRIYA_LIVE_EMBED_MODEL', 'all-minilm')
    assert is_local_url(endpoint), 'This smoke requires a local endpoint'
    provider_root = endpoint.removesuffix('/v1')
    evidence = tmp_path / 'evidence'
    evidence.mkdir()

    def metadata(route):
        with urllib.request.urlopen(provider_root + route, timeout=15) as response:
            return json.load(response)

    # No name-based inference: capture provider version and actual artifact digests.
    version = metadata('/api/version')
    tags = metadata('/api/tags')
    names = {name if ':' in name.rsplit('/', 1)[-1] else name + ':latest'
             for name in (model, embedding)}
    selected = [item for item in tags['models'] if item.get('name') in names]
    assert {item.get('name') for item in selected if item.get('digest')} == names, tags
    runtime = {'endpoint': endpoint, 'provider': 'ollama', 'version': version,
               'models': selected, 'python': sys.version}
    (evidence / 'runtime.json').write_text(json.dumps(runtime, indent=2))

    workspace = tmp_path / 'repo'
    workspace.mkdir()
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    (workspace / 'README.md').write_text('Disposable PRD-003 live smoke repository\n')
    subprocess.run(['git', 'add', 'README.md'], cwd=workspace, check=True)
    subprocess.run(['git', '-c', 'user.name=Kriya Test', '-c', 'user.email=test@kriya.local',
                    'commit', '-qm', 'scratch baseline'], cwd=workspace, check=True)
    root = Path(__file__).resolve().parents[1]
    # Kriya's own smoke bounds (tests/_live_smoke_contract.py): tightened
    # output and generation budgets, so the run ends typed inside the limit.
    cfg = {'llm': {'model': model, 'base_url': endpoint, 'temperature': 0.2, **SMOKE_LLM_BOUNDS},
           'embedding': {'model': embedding, 'base_url': endpoint},
           'plugins': {'directory': str(root / 'plugins')},
           'paths': {'skills': './skills', 'memory': './memory'},
           'logging': {'file_enabled': False},
           'autonomy': {'web_lookup_enabled': False, **SMOKE_AUTONOMY_BOUNDS}}
    config_text = yaml.safe_dump(cfg)
    (workspace / 'kriya.yaml').write_text(config_text)
    (evidence / 'config.sha256').write_text(hashlib.sha256(config_text.encode()).hexdigest())
    (evidence / 'revision.txt').write_text(subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=root, text=True))
    state_dir = str(tmp_path / 'state')
    env = dict(os.environ, PYTHONPATH=str(root), KRIYA_AUTHORITY_HOME=str(tmp_path / 'authority'),
               KRIYA_STATE_DIR=state_dir)
    # Approves only this test's explicit configuration, in a disposable trust store.
    approval = subprocess.run([sys.executable, '-m', 'kriya.cli', 'authority', 'approve', '--confirm'],
                              cwd=workspace, env=env, capture_output=True, text=True, timeout=30)
    (evidence / 'approval.txt').write_text(approval.stdout + approval.stderr)
    assert approval.returncode == 0, approval.stderr
    effective = subprocess.run([sys.executable, '-m', 'kriya.cli', 'config'],
                               cwd=workspace, env=env, capture_output=True, text=True, timeout=30)
    assert effective.returncode == 0, effective.stderr
    effective_config = json.loads(effective.stdout)
    (evidence / 'effective-config.json').write_text(json.dumps(effective_config, indent=2))
    (evidence / 'effective-config.sha256').write_text(hashlib.sha256(
        json.dumps(effective_config, sort_keys=True).encode()).hexdigest())
    before = workspace_snapshot(workspace)
    result = run_reporting_timeout([
        sys.executable, '-m', 'kriya.cli', 'generate',
        'Write add.py containing a Python function add(a, b) returning a + b.',
        '-y', '--json', '--knowledge-policy', 'permissive',
    ], cwd=workspace, env=env, capture_output=True, text=True, timeout=SMOKE_PROCESS_LIMIT_SECONDS)
    (evidence / 'stdout.json').write_text(result.stdout)
    (evidence / 'stderr.txt').write_text(result.stderr)
    (evidence / 'exit-code.txt').write_text(str(result.returncode))
    print(f'PRD-003 live evidence: {evidence}')
    payload = json.loads(result.stdout)  # Rejects narrative AND multiple JSON objects.
    assert isinstance(payload, dict)
    assert 'run_id' in payload, 'Setup failure alone does not prove real workflow execution'
    assert 'Generation Workflow Completed' in result.stderr
    assert result.returncode == (0 if payload.get('quality_gates_passed') else 1)

    evidence_record = assert_smoke_contract(workspace, payload, before=before, stderr=result.stderr,
                                            state_dir=state_dir, model=model)
    (evidence / 'smoke-contract.json').write_text(json.dumps(evidence_record, indent=2))
    if payload.get('generation_metrics') is not None:
        assert payload['generation_metrics'].get('llm', {}).get('calls', 0) > 0, \
            'A real model call must be recorded; setup-only outcomes do not qualify'
