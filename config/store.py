from pathlib import Path
import yaml


class ConfigStore:
    def __init__(self):
        self.config_dir = Path.home() / ".backfup"
        self.config_file = self.config_dir / "config.yaml"
        self.config_dir.mkdir(exist_ok=True)

    def exists(self) -> bool:
        return self.config_file.exists()

    def save(self, config_data: dict) -> None:
        with self.config_file.open("w") as f:
            yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

    def load(self) -> dict:
        if not self.exists():
            raise FileNotFoundError(
                f"No configuration file found at {self.config_file}. "
                "Run `backfup init` to create one."
            )
        with self.config_file.open("r") as f:
            return yaml.safe_load(f) or {}