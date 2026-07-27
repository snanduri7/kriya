import os
from typing import List, Optional, Dict, Any
import yaml
from pydantic import BaseModel, Field

class LLMConfig(BaseModel):
    provider: str = Field(default="openai")
    model: str = Field(default="llama3")
    api_key: str = Field(default="local-key")
    base_url: str = Field(default="http://localhost:11434/v1")
    temperature: float = Field(default=0.2)
    max_tokens: int = Field(default=4096)
    extra_body: Dict[str, Any] = Field(default_factory=dict)

class PluginsConfig(BaseModel):
    directory: str = Field(default="./plugins")
    enabled: List[str] = Field(default_factory=list)

class PathsConfig(BaseModel):
    skills: str = Field(default="./skills")
    memory: str = Field(default="./memory")
    logs: str = Field(default="./logs")

class LoggingConfig(BaseModel):
    level: str = Field(default="INFO")
    file: Optional[str] = Field(default="./logs/kriya.log")

class MCPServerConfig(BaseModel):
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)

class EmbeddingConfig(BaseModel):
    model: str = Field(default="nomic-embed-text:latest")
    base_url: str = Field(default="http://localhost:11434/v1")

class AutonomyConfig(BaseModel):
    mode: str = Field(default="human-in-the-loop")
    sensitive_paths: List[str] = Field(default_factory=lambda: [
        r".*\.env$", r".*secrets.*", r"\.github/workflows/.*", r"Jenkinsfile", 
        r".*credentials.*", r".*password.*"
    ])
    risk_threshold_lines: int = Field(default=500)

class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    mcp: Dict[str, MCPServerConfig] = Field(default_factory=dict)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)

def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from a YAML file, merging with default configs."""
    config_dict = {}
    
    # Try to load default configuration from package path
    default_path = os.path.join(os.path.dirname(__file__), "default_config.yaml")
    if os.path.exists(default_path):
        try:
            with open(default_path, "r") as f:
                default_data = yaml.safe_load(f)
                if default_data:
                    config_dict.update(default_data)
        except Exception:
            pass
            
    # Load user config if specified and exists
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                user_data = yaml.safe_load(f)
                if user_data:
                    # Simple deep merge of level-1 dicts
                    for key, val in user_data.items():
                        if isinstance(val, dict) and key in config_dict and isinstance(config_dict[key], dict):
                            config_dict[key].update(val)
                        else:
                            config_dict[key] = val
        except Exception as e:
            raise ValueError(f"Failed to load configuration at {config_path}: {e}") from e
            
    return AppConfig(**config_dict)
