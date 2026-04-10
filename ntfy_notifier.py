import logging
import requests
from typing import Optional

logger = logging.getLogger("Watcher.Ntfy")


class NtfyNotifier:
    """Publish to an ntfy topic (https://ntfy.sh or self-hosted). Set NTFY_URL to the full topic URL."""

    def __init__(self, topic_url: Optional[str]):
        self.topic_url = topic_url

    def _send(self, title: str, body: str) -> None:
        if not self.topic_url:
            return
        try:
            headers = {"Title": title[:200] if title else "Watcher"}
            requests.post(
                self.topic_url,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=10,
            ).raise_for_status()
        except Exception as e:
            logger.error(f"ntfy notify failed: {e}")

    def notify_recreation_started(self, name: str, image: str) -> None:
        self._send(f"Updating: {name}", f"Pulling and recreating to {image}...")

    def notify_update_success(self, name: str, image: str) -> None:
        self._send(f"Update OK: {name}", f"Container is healthy on {image}.")

    def notify_dependents_restarted(
        self,
        restarted: list[str],
        failures: list[tuple[str, str]],
    ) -> None:
        if not restarted and not failures:
            return
        lines = []
        if restarted:
            lines.append(f"Restarted: {', '.join(restarted)}")
        if failures:
            for n, err in failures:
                lines.append(f"Failed {n}: {err}")
        self._send("Dependent restarts", "\n".join(lines))

    def notify_failure(self, name: str, reason: str) -> None:
        self._send(f"FAILED: {name}", f"Reason: {reason}")

    def notify_rollback(self, name: str, success: bool, detail: str = "") -> None:
        title = f"Rollback OK: {name}" if success else f"ROLLBACK FAILED: {name}"
        self._send(title, detail)

    def notify_summary_report(self, summary: dict) -> None:
        if not summary["updated"] and not summary["failed"] and not summary["rolled_back"] and not summary.get("reported"):
            return
        lines = ["Watcher Scan Summary"]
        if summary.get("reported"):
            lines.append(f"Updates available (not auto-updated): {', '.join(summary['reported'])}")
        if summary["updated"]:
            lines.append(f"Updated: {', '.join(summary['updated'])}")
        if summary["failed"]:
            lines.append(f"Failed: {', '.join(summary['failed'])}")
        if summary["rolled_back"]:
            lines.append(f"Rolled back: {', '.join(summary['rolled_back'])}")
        self._send("Cycle complete", "\n".join(lines))

    def notify_summary(self, title: str, message: str) -> None:
        self._send(title, message)

    def notify_startup(self, check_interval_seconds: int, hostname: str) -> None:
        if check_interval_seconds >= 86400:
            iv = f"{check_interval_seconds // 86400}d"
        elif check_interval_seconds >= 3600:
            iv = f"{check_interval_seconds // 3600}h"
        elif check_interval_seconds >= 60:
            iv = f"{check_interval_seconds // 60}m"
        else:
            iv = f"{check_interval_seconds}s"
        self._send(
            "Watcher started",
            f"Monitoring containers.\nHost: {hostname}\nScan interval: {iv} ({check_interval_seconds}s)",
        )

    def notify_shutdown(self, reason: str = "Stopped by user (Ctrl+C).") -> None:
        self._send("Watcher stopped", reason)
