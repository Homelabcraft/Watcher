import time
import logging
import docker
import sys

from config import Config
from discord_notifier import DiscordNotifier
from docker_handler import DockerHandler
from health_monitor import HealthMonitor
from docker.models.containers import Container

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger('Watcher')

class WatcherService:
    def __init__(self):
        self.config = Config()
        try:
            self.client = docker.from_env()
        except Exception as e:
            logger.critical(f"Docker connection failed: {e}")
            sys.exit(1)
            
        self.docker = DockerHandler(self.client, self.config)
        self.notifier = DiscordNotifier(self.config.discord_webhook_url)
        self.health = HealthMonitor(self.client)

    def process_container(self, container: Container) -> str:
        """Standard production update lifecycle with rename backup protection."""
        name = container.name
        old_id = container.image.id
        ref = self.docker.get_image_ref(container)
        recreate_started = False
        
        try:
            # 1. Update Detection
            status = self.docker.check_for_update(container)
            if status == "no_update": return "no_update"
            if status == "skipped_dry_run":
                logger.info(f"[DRY] Would check updates for {name}")
                return "skipped_dry_run"
            if status == "error":
                self.notifier.notify_failure(name, "Update check failed.")
                return "failed"

            # 2. State Capture
            container.reload()
            plan = self.docker.get_recreation_plan(container)

            # 3. Execution
            logger.info(f"Updating {name}...")
            self.notifier.notify_recreation_started(name, ref)
            
            recreate_started = True
            self.docker.recreate(name, plan)

            # 4. Verification
            if self.health.wait_for_health(name, self.config.health_check_retries, self.config.health_check_delay):
                logger.info(f"Success: {name}")
                self.docker.remove_backup(name)
                
                # 5. Cleanup (Only after success)
                if self.config.cleanup_old_images:
                    self.docker.remove_image(old_id)
                return "updated"
            else:
                logger.error(f"Health failed: {name}. Rolling back...")
                self.notifier.notify_failure(name, "Unhealthy post-update.")
                self.perform_rollback(name, old_id)
                return "rolled_back"

        except Exception as e:
            logger.error(f"Error processing {name}: {e}")
            self.notifier.notify_failure(name, f"Unexpected error: {str(e)}")
            if recreate_started:
                logger.info(f"Attempting rollback for {name} after execution error...")
                self.perform_rollback(name, old_id)
            return "failed"

    def perform_rollback(self, name: str, old_id: str):
        """Rollback using the saved backup container."""
        logger.warning(f"ROLLBACK for {name}")
        try:
            # 1. Inspect current container under target name
            try:
                current_container = self.client.containers.get(name)
                if current_container.image.id == old_id:
                    logger.info(f"Original container {name} is still in place (rename likely failed). Starting it...")
                    current_container.start()
                    self.notifier.notify_rollback(name, True, "Original container recovered (update didn't complete).")
                    return
                else:
                    logger.info(f"Removing failed new container {name}...")
                    current_container.remove(force=True)
            except docker.errors.NotFound:
                pass # No container under this name, proceed to restore backup

            # 2. Restore backup
            backup_name = f"{name}_backup"
            try:
                backup = self.client.containers.get(backup_name)
                backup.rename(name)
                backup.start()
                logger.info(f"Restored {name} from backup.")
            except docker.errors.NotFound:
                logger.error(f"Backup {backup_name} not found. Rollback failed.")
                self.notifier.notify_rollback(name, False, "Backup container not found.")
                return

            if self.health.wait_for_health(name, self.config.health_check_retries, self.config.health_check_delay):
                self.notifier.notify_rollback(name, True, "Restored from backup.")
            else:
                self.notifier.notify_rollback(name, False, "Container stopped or unhealthy after rollback attempt.")
        except Exception as e:
            logger.error(f"CRITICAL ROLLBACK FAILURE: {e}")
            self.notifier.notify_rollback(name, False, f"Critical error during rollback: {str(e)}")

    def run_cycle(self):
        logger.info("--- Cycle Start ---")
        watched = self.docker.get_watched_containers()
        summary = {"updated": [], "failed": [], "rolled_back": []}
        
        for c in watched:
            status = self.process_container(c)
            if status == "updated":
                summary["updated"].append(c.name)
            elif status == "failed":
                summary["failed"].append(c.name)
            elif status == "rolled_back":
                summary["rolled_back"].append(c.name)
                
        self.notifier.notify_summary_report(summary)
        logger.info("--- Cycle End ---")

    def start(self):
        logger.info("Watcher started.")
        self.notifier.notify_summary("Watcher Started", "Service is now monitoring containers.")
        try:
            while True:
                self.run_cycle()
                time.sleep(self.config.check_interval)
        except KeyboardInterrupt:
            logger.info("Stopping...")
        except Exception as e:
            logger.critical(f"Crashed: {e}")

if __name__ == "__main__":
    WatcherService().start()
