import logging
import os
import re

from dotenv import load_dotenv

from exceptions import ConfigurationError

if "UNITTEST_MODE" not in os.environ:
    load_dotenv()

logger = logging.getLogger('Watcher.Config')

class Config:
    """Production configuration for Watcher."""
    def __init__(self):
        # Operational
        self.check_interval = self._parse_int("CHECK_INTERVAL", 86400)
        self.schedule_time = os.getenv("SCHEDULE_TIME")
        self.discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        self.slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL")
        self.ntfy_url = os.getenv("NTFY_URL")
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.dry_run = self._parse_bool("DRY_RUN", False)
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        
        # Notifications
        self.notify_updates_available = self._parse_bool("NOTIFY_UPDATES_AVAILABLE", True)
        self.notify_on_update_start = self._parse_bool("NOTIFY_ON_UPDATE_START", True)
        self.notify_summary_strategy = os.getenv("NOTIFY_SUMMARY_STRATEGY", "always").lower() # always, on_change, on_error
        
        # Registry Auth
        self.reg_user = os.getenv("REGISTRY_USERNAME")
        self.reg_pass = os.getenv("REGISTRY_PASSWORD")
        
        # Selection & Exclusion
        self.watch_by_label = self._parse_bool("WATCH_BY_LABEL", True)
        self.watch_label_key = os.getenv("WATCH_LABEL_KEY", "watcher.enable")
        self.watch_label_value = os.getenv("WATCH_LABEL_VALUE", "true")
        
        exclude_env = os.getenv("EXCLUDE_CONTAINER_NAMES", "")
        self.exclude_names = [n.strip() for n in exclude_env.split(",") if n.strip()]
        if "watcher" not in self.exclude_names:
            self.exclude_names.append("watcher")
            
        self.exclude_regex = os.getenv("EXCLUDE_CONTAINER_REGEX")

        # Dependency Restarts
        self.depends_on_label_key = os.getenv("DEPENDS_ON_LABEL_KEY", "watcher.depends_on")

        # Health Check
        self.health_check_retries = self._parse_int("HEALTH_CHECK_RETRIES", 12)
        self.health_check_delay = self._parse_int("HEALTH_CHECK_DELAY", 10)

        # Cleanup
        self.cleanup_old_images = self._parse_bool("CLEANUP_OLD_IMAGES", False)
        
        # 1.6.0 Cooldown & Journal
        self.failure_cooldown_seconds = self._parse_int("FAILURE_COOLDOWN_SECONDS", 3600)
        self.max_retries_before_cooldown = self._parse_int("MAX_RETRIES_BEFORE_COOLDOWN", 1)
        self.journal_enabled = self._parse_bool("JOURNAL_ENABLED", True)
        self.journal_path = os.getenv("JOURNAL_PATH", "/app/data/journal.json")
        self.state_path = os.getenv("STATE_PATH", "/app/data/state.json")
        self.journal_max_entries = self._parse_int("JOURNAL_MAX_ENTRIES", 100)
        self.allow_user_fallback = self._parse_bool("ALLOW_USER_FALLBACK", False)
        
        # New 1.6.0 Options
        self.max_updates_per_cycle = self._parse_int("MAX_UPDATES_PER_CYCLE", 0) # 0 = unlimited
        self.restart_dependents = self._parse_bool("RESTART_DEPENDENTS", True)
        self.notify_on_startup = self._parse_bool("NOTIFY_ON_STARTUP", True)

        self._validate()
        
        if self.schedule_time:
            logger.info(f"Watcher Config: ScheduleTime={self.schedule_time}, DryRun={self.dry_run}, Cleanup={self.cleanup_old_images}")
        else:
            logger.info(f"Watcher Config: Interval={self.check_interval}s, DryRun={self.dry_run}, Cleanup={self.cleanup_old_images}")

    def _parse_int(self, key: str, default: int) -> int:
        val = os.getenv(key)
        if val is None:
            return default
        try:
            return int(val)
        except ValueError:
            raise ConfigurationError(f"Environment variable {key} must be an integer. Got: '{val}'")

    def _parse_bool(self, key: str, default: bool) -> bool:
        val = os.getenv(key)
        if val is None:
            return default
        val_lower = val.lower()
        if val_lower in ("true", "1", "yes", "on"):
            return True
        if val_lower in ("false", "0", "no", "off"):
            return False
        raise ConfigurationError(f"Environment variable {key} must be a boolean (true/false, 1/0). Got: '{val}'")

    def _validate(self):
        """Fail fast validation of configuration values."""
        if self.schedule_time:
            if not re.match(r"^([01][0-9]|2[0-3]):[0-5][0-9]$", self.schedule_time.strip()):
                raise ConfigurationError(f"SCHEDULE_TIME must be in HH:MM format (24-hour). Got: '{self.schedule_time}'")
        else:
            if self.check_interval < 10:
                raise ConfigurationError("CHECK_INTERVAL must be at least 10 seconds to prevent aggressive API rate limiting.")
            
        if self.health_check_retries < 0:
            raise ConfigurationError("HEALTH_CHECK_RETRIES cannot be negative.")
            
        if self.health_check_delay < 1:
            raise ConfigurationError("HEALTH_CHECK_DELAY must be at least 1 second.")

        if self.discord_webhook_url:
            if not (self.discord_webhook_url.startswith("http://") or self.discord_webhook_url.startswith("https://")):
                raise ConfigurationError("DISCORD_WEBHOOK_URL must start with http:// or https://")

        if self.slack_webhook_url:
            if not (self.slack_webhook_url.startswith("http://") or self.slack_webhook_url.startswith("https://")):
                raise ConfigurationError("SLACK_WEBHOOK_URL must start with http:// or https://")

        if self.ntfy_url:
            if not (self.ntfy_url.startswith("http://") or self.ntfy_url.startswith("https://")):
                raise ConfigurationError("NTFY_URL must start with http:// or https://")

        if bool(self.telegram_bot_token) != bool(self.telegram_chat_id):
            raise ConfigurationError("Both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set to enable Telegram notifications.")

        if self.exclude_regex:
            try:
                re.compile(self.exclude_regex)
            except re.error as e:
                raise ConfigurationError(f"Invalid EXCLUDE_CONTAINER_REGEX: {e}")

        if self.max_updates_per_cycle < 0:
            raise ConfigurationError("MAX_UPDATES_PER_CYCLE cannot be negative.")

        if self.journal_max_entries < 1:
            raise ConfigurationError("JOURNAL_MAX_ENTRIES must be at least 1.")

        if self.notify_summary_strategy not in ("always", "on_change", "on_error"):
            raise ConfigurationError("NOTIFY_SUMMARY_STRATEGY must be one of: always, on_change, on_error")

        if bool(self.reg_user) != bool(self.reg_pass):
            logger.warning("Registry authentication: Only one of REGISTRY_USERNAME or REGISTRY_PASSWORD is set. Auth might fail if both are required.")
