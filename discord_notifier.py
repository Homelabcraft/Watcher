import logging
import requests
import datetime
from typing import Optional

logger = logging.getLogger('Watcher.Discord')

class DiscordNotifier:
    """Sends structured Discord notifications for the update lifecycle."""
    def __init__(self, webhook_url: Optional[str]):
        self.webhook_url = webhook_url

    def _send(self, payload: dict):
        if not self.webhook_url: return
        try:
            requests.post(self.webhook_url, json=payload, timeout=10).raise_for_status()
        except Exception as e:
            logger.error(f"Discord notify failed: {e}")

    def send_event(self, title: str, description: str, color: int = 0x3498db, fields: list = None):
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "fields": fields or []
        }
        self._send({"embeds": [embed]})

    def notify_update(self, name: str, image: str, old_id: str, new_id: str):
        fields = [
            {"name": "Image", "value": f"`{image}`", "inline": False},
            {"name": "Old ID", "value": f"`{old_id[:12]}`", "inline": True},
            {"name": "New ID", "value": f"`{new_id[:12]}`", "inline": True}
        ]
        self.send_event(f"Update: {name}", "A new version is available.", color=0xf1c40f, fields=fields)

    def notify_success(self, name: str, image: str):
        self.send_event(f"Success: {name}", f"Updated to latest `{image}`.", color=0x2ecc71)

    def notify_recreation_started(self, name: str):
        self.send_event(f"Updating: {name}", "Recreating container...", color=0x3498db)

    def notify_failure(self, name: str, reason: str):
        self.send_event(f"FAILED: {name}", f"Reason: {reason}", color=0xe74c3c)

    def notify_rollback(self, name: str, success: bool, detail: str = ""):
        title = f"Rollback OK: {name}" if success else f"ROLLBACK FAILED: {name}"
        color = 0x2ecc71 if success else 0xc0392b
        self.send_event(title, detail, color=color)

    def notify_summary(self, title: str, message: str):
        self.send_event(title, message, color=0x95a5a6)
