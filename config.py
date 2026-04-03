import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('Watcher.Config')

class Config:
    """Production configuration for Watcher."""
    def __init__(self):
        # Operational
        self.check_interval = self._parse_int("CHECK_INTERVAL", 300)
        self.discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        self.dry_run = self._parse_bool("DRY_RUN", False)
        
        # Registry Auth
        self.reg_user = os.getenv("REGISTRY_USERNAME")
        self.reg_pass = os.getenv("REGISTRY_PASSWORD")
        
        # Selection
        self.watch_by_label = self._parse_bool("WATCH_BY_LABEL", False)
        self.watch_label_key = os.getenv("WATCH_LABEL_KEY", "watcher.enable")
        self.watch_label_value = os.getenv("WATCH_LABEL_VALUE", "true")
        
        # Self-Protection & Exclusions
        self.exclude_names = [n.strip() for n in os.getenv("EXCLUDE_CONTAINER_NAMES", "").split(",") if n.strip()]

        # Health Check
        self.health_check_retries = self._parse_int("HEALTH_CHECK_RETRIES", 12)
        self.health_check_delay = self._parse_int("HEALTH_CHECK_DELAY", 10)

        # Cleanup
        self.cleanup_old_images = self._parse_bool("CLEANUP_OLD_IMAGES", False)

        logger.info(f"Watcher Config: Interval={self.check_interval}s, DryRun={self.dry_run}, Cleanup={self.cleanup_old_images}")

    def _parse_int(self, key: str, default: int) -> int:
        val = os.getenv(key)
        try:
            return int(val) if val is not None else default
        except ValueError:
            return default

    def _parse_bool(self, key: str, default: bool) -> bool:
        val = os.getenv(key, str(default)).lower()
        return val in ("true", "1", "yes", "on")
