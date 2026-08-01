import json
import logging
import os
from datetime import datetime

logger = logging.getLogger('Watcher.StateStore')

class StateStore:
    """Manages persistent state across Watcher restarts (e.g. cooldowns)."""
    
    def __init__(self, path: str):
        self.path = path
        self._data = {"cooldowns": {}}
        self._load()
        
    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        # Convert ISO strings back to datetime
                        if "cooldowns" in data:
                            for k, v in data["cooldowns"].items():
                                if "cooldown_until" in v and isinstance(v["cooldown_until"], str):
                                    try:
                                        v["cooldown_until"] = datetime.fromisoformat(v["cooldown_until"])
                                    except ValueError:
                                        v["cooldown_until"] = datetime.min
                        self._data = data
                    else:
                        logger.warning(f"State store at {self.path} is invalid format. Backing up and resetting.")
                        import shutil
                        try: shutil.copy(self.path, f"{self.path}.corrupted")
                        except: pass
                        self._data = {"cooldowns": {}}
            except Exception as e:
                logger.error(f"Failed to load state store: {e}")
                logger.warning(f"State store at {self.path} is corrupted. Backing up and resetting.")
                import shutil
                try: shutil.copy(self.path, f"{self.path}.corrupted")
                except: pass
                self._data = {"cooldowns": {}}

    def _save(self):
        try:
            # Prepare data for JSON serialization (datetime to string)
            to_save = {"cooldowns": {}}
            for k, v in self._data["cooldowns"].items():
                to_save["cooldowns"][k] = {
                    "count": v.get("count", 0),
                    "cooldown_until": v["cooldown_until"].isoformat() if isinstance(v.get("cooldown_until"), datetime) else datetime.min.isoformat()
                }
                
            os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
            temp_path = f"{self.path}.tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(to_save, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.path)
        except Exception as e:
            logger.error(f"Failed to save state store: {e}")
            if os.path.exists(f"{self.path}.tmp"):
                try: os.remove(f"{self.path}.tmp")
                except: pass

    def get_cooldowns(self) -> dict:
        return self._data["cooldowns"]

    def set_cooldowns(self, cooldowns: dict):
        self._data["cooldowns"] = cooldowns
        self._save()
