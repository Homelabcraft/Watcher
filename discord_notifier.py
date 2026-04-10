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

    def notify_recreation_started(self, name: str, image: str):
        self.send_event(f"Updating: {name}", f"Pulling and recreating to `{image}`...", color=0x3498db)

    def notify_update_success(self, name: str, image: str):
        """Sent after health checks pass and backup cleanup is done."""
        self.send_event(
            f"Update OK: {name}",
            f"Container is healthy on `{image}`.",
            color=0x27ae60,
        )

    def notify_failure(self, name: str, reason: str):
        self.send_event(f"FAILED: {name}", f"Reason: {reason}", color=0xe74c3c)

    def notify_rollback(self, name: str, success: bool, detail: str = ""):
        title = f"Rollback OK: {name}" if success else f"ROLLBACK FAILED: {name}"
        color = 0x2ecc71 if success else 0xc0392b
        self.send_event(title, detail, color=color)

    def notify_summary_report(self, summary: dict):
        if not summary["updated"] and not summary["failed"] and not summary["rolled_back"] and not summary.get("reported"):
            return

        lines = ["📊 **Watcher Scan Summary**"]
        if summary.get("reported"):
            lines.append(f"👀 **Updates Available (Not Auto-Updated):** {', '.join(summary['reported'])}")
        if summary["updated"]:
            lines.append(f"✅ **Updated:** {', '.join(summary['updated'])}")
        if summary["failed"]:
            lines.append(f"❌ **Failed:** {', '.join(summary['failed'])}")
        if summary["rolled_back"]:
            lines.append(f"⚠️ **Rolled Back:** {', '.join(summary['rolled_back'])}")

        self.send_event("Cycle Complete", "\n".join(lines), color=0x9b59b6)

    def notify_summary(self, title: str, message: str):
        self.send_event(title, message, color=0x95a5a6)
