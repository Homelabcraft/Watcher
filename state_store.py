import copy
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from exceptions import StateStoreError

logger = logging.getLogger('Watcher.StateStore')

class StateStore:
    """Manages persistent state across Watcher restarts (e.g. cooldowns, transactions)."""
    
    def __init__(self, path: str, tz: str = "UTC"):
        self.path = path
        self.tz = tz
        self._data = {"cooldowns": {}, "transactions": {}}
        self._load()
        
    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        if "cooldowns" not in data or not isinstance(data["cooldowns"], dict):
                            data["cooldowns"] = {}
                        if "transactions" not in data or not isinstance(data["transactions"], dict):
                            data["transactions"] = {}
                            
                        # Convert ISO strings back to datetime
                        for v in data["cooldowns"].values():
                            if "cooldown_until" in v and isinstance(v["cooldown_until"], str):
                                try:
                                    dt = datetime.fromisoformat(v["cooldown_until"])
                                    if dt.tzinfo is None:
                                        import zoneinfo
                                        dt = dt.replace(tzinfo=zoneinfo.ZoneInfo(self.tz)).astimezone(timezone.utc)
                                    v["cooldown_until"] = dt
                                except ValueError:
                                    v["cooldown_until"] = datetime.min.replace(tzinfo=timezone.utc)
                        self._data = data
                    else:
                        logger.warning(f"State store at {self.path} is invalid format. Backing up and resetting.")
                        try:
                            shutil.copy(self.path, f"{self.path}.corrupted_{int(time.time())}")
                        except Exception as e: # noqa: BLE001
                            logger.error(f"Failed to backup state store: {e}")
                        self._data = {"cooldowns": {}, "transactions": {}}
            except Exception as e: # noqa: BLE001
                logger.error(f"Failed to load state store: {e}")
                logger.warning(f"State store at {self.path} is corrupted. Backing up and resetting.")
                try:
                    shutil.copy(self.path, f"{self.path}.corrupted_{int(time.time())}")
                except Exception as e_copy: # noqa: BLE001
                    logger.error(f"Failed to backup state store: {e_copy}")
                self._data = {"cooldowns": {}, "transactions": {}}

    def _save(self):
        try:
            # Prepare data for JSON serialization (datetime to string)
            to_save = {"cooldowns": {}, "transactions": self._data.get("transactions", {})}
            for k, v in self._data["cooldowns"].items():
                to_save["cooldowns"][k] = {
                    "count": v.get("count", 0),
                    "cooldown_until": v["cooldown_until"].isoformat() if isinstance(v.get("cooldown_until"), datetime) else datetime.min.replace(tzinfo=timezone.utc).isoformat()
                }
                
            os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
            temp_path = f"{self.path}.tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(to_save, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.path)
        except Exception as e: # noqa: BLE001
            logger.error(f"Failed to save state store: {e}")
            if os.path.exists(f"{self.path}.tmp"):
                try:
                    os.remove(f"{self.path}.tmp")
                except Exception as e2: # noqa: BLE001
                    logger.error(f"Failed to clean up temp file: {e2}")
            raise StateStoreError(f"Atomic save failed for state store: {e}")

    def get_cooldowns(self) -> dict:
        return copy.deepcopy(self._data["cooldowns"])

    def set_cooldowns(self, cooldowns: dict):
        old_data = copy.deepcopy(self._data)
        try:
            self._data["cooldowns"] = copy.deepcopy(cooldowns)
            self._save()
        except StateStoreError:
            self._data = old_data
            raise
    def start_transaction(self, container_name: str, original_container_id: str, original_image_id: str, planned_new_image_id: str) -> str:
        """Starts an update transaction for a container."""
        if container_name in self._data.get("transactions", {}):
            raise StateStoreError(f"Transaction already exists for container {container_name}")
            
        old_data = copy.deepcopy(self._data)
        transaction_id = str(uuid.uuid4())
        try:
            self._data["transactions"][container_name] = {
                "transaction_id": transaction_id,
                "container_name": container_name,
                "original_container_id": original_container_id,
                "original_image_id": original_image_id,
                "planned_new_image_id": planned_new_image_id,
                "backup_name": f"{container_name}_backup",
                "backup_container_id": None,
                "new_container_id": None,
                "new_image_id": None,
                "phase": "prepared",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            self._save()
            return transaction_id
        except StateStoreError:
            self._data = old_data
            raise

    def update_transaction(self, container_name: str, phase: str, **kwargs):
        """Updates the status and optional fields of an ongoing transaction."""
        if container_name not in self._data.get("transactions", {}):
            raise StateStoreError(f"No active transaction for container {container_name}")
            
        old_data = copy.deepcopy(self._data)
        try:
            self._data["transactions"][container_name]["phase"] = phase
            for k, v in kwargs.items():
                if v is not None:
                    self._data["transactions"][container_name][k] = v
            self._save()
        except StateStoreError:
            self._data = old_data
            raise

    def end_transaction(self, container_name: str):
        """Removes a completed transaction."""
        if container_name in self._data["transactions"]:
            old_data = copy.deepcopy(self._data)
            try:
                del self._data["transactions"][container_name]
                self._save()
            except StateStoreError:
                self._data = old_data
                raise

    def get_transactions(self) -> dict[str, Any]:
        """Returns all ongoing transactions."""
        return copy.deepcopy(self._data.get("transactions", {}))
