import html
import logging
import requests
from typing import Optional
from models import ContainerUpdateInfo, ExecutionPlan

logger = logging.getLogger("Watcher.Telegram")


class TelegramNotifier:
    """Telegram Bot API (https://core.telegram.org/bots/api#sendmessage)."""

    def __init__(self, bot_token: Optional[str], chat_id: Optional[str]):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def _send(self, title: str, body: str) -> None:
        if not self.bot_token or not self.chat_id:
            return
        if body:
            text = f"<b>{html.escape(title)}</b>\n{html.escape(body)}"
        else:
            text = f"<b>{html.escape(title)}</b>"
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            requests.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            ).raise_for_status()
        except requests.exceptions.Timeout:
            logger.warning("Telegram webhook notification timed out.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram notify failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during Telegram notification: {e}")

    def notify_scan_started(self, total_containers: int, run_mode: str) -> None:
        self._send("🔍 Scan Started", f"Checking {total_containers} containers.\nMode: {run_mode}")

    def notify_update_started(self, name: str, image: str, old_short_id: str, new_short_id: str, current_status: str, dependents: list[str]) -> None:
        msg = f"Target: <code>{image}</code>\nStatus: <code>{current_status}</code>"
        if old_short_id and new_short_id:
            msg += f"\nVersion: <code>{old_short_id}</code> -> <code>{new_short_id}</code>"
        if dependents:
            msg += f"\nDependents: {', '.join(dependents)}"
        self._send(f"🔄 Updating: {name}", msg)

    def notify_update_success(self, info: ContainerUpdateInfo) -> None:
        msg = f"Duration: {info.duration_sec:.1f}s"
        if info.old_image_short_id and info.new_image_short_id:
            msg += f"\nVersion: <code>{info.old_image_short_id}</code> -> <code>{info.new_image_short_id}</code>"
        self._send(f"✅ Update OK: {info.name}", msg)

    def notify_update_failure(self, info: ContainerUpdateInfo) -> None:
        safe_error = html.escape(str(info.error_message))
        msg = f"Failed Step: {info.error_step}\nError: <pre>{safe_error}</pre>"
        if info.rollback_attempted:
            rb_status = "✅ Success" if info.rollback_success else "❌ Failed"
            msg += f"\nRollback: {rb_status} ({info.rollback_details})"
        self._send(f"❌ Update FAILED: {info.name}", msg)

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
        self._send("🔗 Dependent restarts", "\n".join(lines))

    def notify_rollback(self, name: str, status: str, detail: str = "") -> None:
        if status == "started":
            self._send(f"⚠️ Rollback Initiated: {name}", detail)
        elif status == "success":
            self._send(f"✅ Rollback OK: {name}", detail)
        else:
            self._send(f"🚨 ROLLBACK FAILED: {name}", detail)

    def notify_summary_report(self, summary: dict, infos: list[ContainerUpdateInfo] = None, duration_sec: float = 0.0) -> None:
        if not summary["updated"] and not summary["failed"] and not summary["rolled_back"] and not summary.get("reported"):
            return
            
        def format_info(name):
            if not infos: return name
            for info in infos:
                if info.name == name and info.old_image_short_id and info.new_image_short_id:
                    return f"{name} (<code>{info.old_image_short_id}</code> -> <code>{info.new_image_short_id}</code>)"
            return name
            
        lines = []
        if summary.get("reported"):
            lines.append(f"Updates available (not auto-updated):\n- {chr(10) + '- '.join([format_info(n) for n in summary['reported']])}")
        if summary["updated"]:
            lines.append(f"Updated:\n- {chr(10) + '- '.join([format_info(n) for n in summary['updated']])}")
        if summary["failed"]:
            lines.append(f"Failed: {', '.join(summary['failed'])}")
        if summary["rolled_back"]:
            lines.append(f"Rolled back: {', '.join(summary['rolled_back'])}")
        self._send(f"📊 Watcher Scan Summary ({duration_sec:.1f}s)", "\n".join(lines))

    def notify_execution_plan(self, plan: ExecutionPlan, next_run: str = None) -> None:
        if not plan.updates_available and not plan.monitored_only:
            return

        lines = [f"Containers checked: {plan.checked_containers}"]
        
        if plan.updates_available:
            lines.append("\nWould Update:")
            for u in plan.updates_available:
                if u.old_image_short_id and u.new_image_short_id:
                    lines.append(f"- {u.name} (<code>{u.old_image_short_id}</code> -> <code>{u.new_image_short_id}</code>)")
                else:
                    lines.append(f"- {u.name} (Update found)")

        if plan.monitored_only:
            lines.append("\nMonitored Only:")
            for u in plan.monitored_only:
                if u.old_image_short_id and u.new_image_short_id:
                    lines.append(f"- {u.name} (<code>{u.old_image_short_id}</code> -> <code>{u.new_image_short_id}</code>)")
                else:
                    lines.append(f"- {u.name} (Update found)")

        if plan.dependents_to_restart:
            lines.append(f"\nWould Restart Dependents: {', '.join(plan.dependents_to_restart)}")

        if next_run:
            lines.append(f"\nNext scheduled run: {next_run}")

        self._send("📋 Dry Run Execution Plan", "\n".join(lines))

    def notify_summary(self, title: str, message: str) -> None:
        self._send(title, message)

    def notify_startup(self, config: dict, hostname: str) -> None:
        iv = config.get("check_interval", 0)
        if iv >= 86400: iv_str = f"{iv // 86400}d"
        elif iv >= 3600: iv_str = f"{iv // 3600}h"
        elif iv >= 60: iv_str = f"{iv // 60}m"
        else: iv_str = f"{iv}s"
        
        mode = "SCHEDULED" if config.get("schedule_time") else "INTERVAL"
        time_info = f"{config.get('schedule_time')}" if config.get("schedule_time") else f"{iv_str} ({iv}s)"
        
        self._send(
            "🚀 Watcher started",
            f"Host: <code>{hostname}</code>\nMode: <code>{mode}</code>\nInterval/Time: {time_info}\nDry Run: {'Active' if config.get('dry_run') else 'Disabled'}"
        )

    def notify_shutdown(self, reason: str = "Stopped by user (Ctrl+C).") -> None:
        self._send("🛑 Watcher stopped", reason)