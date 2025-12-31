"""Configuration management utilities"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()


def load_config(config_path: str = None) -> Dict[str, Any]:
    """
    Load configuration from YAML file

    Args:
        config_path: Path to config file. If None, uses default location

    Returns:
        Configuration dictionary
    """
    if config_path is None:
        config_path = os.path.join(
            Path(__file__).parent.parent.parent, "config", "config.yaml"
        )

    # Fall back to example config if main config doesn't exist
    if not os.path.exists(config_path):
        config_path = config_path.replace("config.yaml", "config.example.yaml")

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    return config


def get_env(key: str, default: Any = None) -> Any:
    """
    Get environment variable with optional default

    Args:
        key: Environment variable key
        default: Default value if not found

    Returns:
        Environment variable value or default
    """
    return os.getenv(key, default)
