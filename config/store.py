from pathlib import Path
class ConfigStore:
    def __init__(self):
        self.config_dir = Path.home() / ".backfup"
        self.config_file = self.config_dir / "config.yaml"