import json
import logging
import os
from datetime import datetime
from typing import Any, List, Dict

logger = logging.getLogger('Watcher.Journal')

class Journal:
    """Manages a persistent local history of scan cycles and updates."""
    def __init__(self, enabled: bool, path: str, max_entries: int = 100):
        self.enabled = enabled
        self.path = path
        self.max_entries = max_entries
        self._history: List[Dict[str, Any]] = []
        
        if self.enabled:
            self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    self._history = json.load(f)
                    if not isinstance(self._history, list):
                        logger.warning(f"Journal at {self.path} is invalid. Resetting.")
                        self._history = []
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load journal: {e}")
                self._history = []
            except Exception as e:
                logger.error(f"Unexpected journal load error: {e}")
                self._history = []

    def _save(self):
        if not self.enabled:
            return
        try:
            # History rotation: Keep only the configured max_entries
            to_save = self._history[-self.max_entries:]
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(to_save, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Failed to save journal (IO Error): {e}")
        except Exception as e:
            logger.error(f"Unexpected journal save error: {e}")

    def record_cycle(self, 
                     total_checked: int, 
                     run_mode: str, 
                     summary: Dict[str, List[str]], 
                     all_infos: List[Any], 
                     duration_sec: float):
        """Adds a new cycle record to the history."""
        if not self.enabled:
            return

        # Convert ContainerUpdateInfo objects to serializable dicts
        events = []
        for info in all_infos:
            events.append({
                "name": info.name,
                "status": str(info.status.name),
                "error_step": info.error_step,
                "error_message": info.error_message,
                "old_image": info.old_image_short_id,
                "new_image": info.new_image_short_id,
                "duration": round(getattr(info, "duration_sec", 0.0), 2),
                "rollback": {
                    "attempted": getattr(info, "rollback_attempted", False),
                    "success": getattr(info, "rollback_success", False),
                    "details": getattr(info, "rollback_details", None)
                }
            })

        entry = {
            "timestamp": datetime.now().isoformat(),
            "run_mode": run_mode,
            "duration_sec": round(duration_sec, 2),
            "checked_containers": total_checked,
            "summary": summary,
            "events": events
        }

        self._history.append(entry)
        self._save()
        logger.debug(f"Journal entry added for cycle at {entry['timestamp']}")
