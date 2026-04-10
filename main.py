import time
import logging
import docker
import sys
import socket
from typing import Optional, Any

from config import Config
from notifier_factory import build_notifier
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
        self.notifier = build_notifier(self.config)
        self.health = HealthMonitor(self.client)

    def process_container(self, container: Container, auto_update: bool) -> tuple[str, Optional[str]]:
        """Standard production update lifecycle with rename backup protection."""
        name = container.name
        old_container_id = container.id
        old_image_id = container.image.id
        ref = self.docker.get_image_ref(container)
        recreate_started = False
        
        try:
            # 1. Update Detection
            status = self.docker.check_for_update(container)
            if status == "no_update":
                return "no_update", None
            if status == "skipped_dry_run":
                logger.info(f"[DRY] Would check updates for {name}")
                return "skipped_dry_run", None
            if status == "error":
                logger.error(f"Update check failed for {name}")
                self.notifier.notify_failure(name, "Update check failed.")
                return "failed", None

            # Hybrid Mode: If not auto_update, we stop here and just report
            if not auto_update:
                logger.info(f"Update available for {name}, but auto-update is not enabled. Reporting only.")
                return "reported", None

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
                self.notifier.notify_update_success(name, ref)

                # 5. Cleanup (Only after success)
                if self.config.cleanup_old_images:
                    self.docker.remove_image(old_image_id)
                return "updated", old_container_id
            else:
                logger.error(f"Health failed: {name}. Rolling back...")
                self.notifier.notify_failure(name, "Unhealthy post-update.")
                self.perform_rollback(name, old_image_id)
                return "rolled_back", None

        except Exception as e:
            logger.error(f"Error processing {name}: {e}")
            self.notifier.notify_failure(name, f"Unexpected error: {str(e)}")
            if recreate_started:
                logger.info(f"Attempting rollback for {name} after execution error...")
                self.perform_rollback(name, old_image_id)
            return "failed", None

    def perform_rollback(self, name: str, old_id: str):
        """Rollback using the saved backup container."""
        logger.warning(f"ROLLBACK for {name}")
        try:
            # 1. Inspect current container under target name
            try:
                current_container = self.client.containers.get(name)
                # Check image ID to see if update actually happened
                if current_container.image.id == old_id:
                    logger.info(f"Original container {name} is still in place (rename likely failed). Starting it...")
                    current_container.start()
                    self.notifier.notify_rollback(name, True, "Original container recovered (update didn't complete).")
                    return
                else:
                    logger.info(f"Removing failed new container {name}...")
                    current_container.remove(force=True)
            except docker.errors.NotFound:
                logger.debug(f"No container found with name {name} during rollback, proceeding to restore backup.")
            except Exception as e:
                logger.error(f"Error handling current container during rollback: {e}")

            # 2. Restore backup
            backup_name = f"{name}_backup"
            try:
                backup = self.client.containers.get(backup_name)
                logger.info(f"Found backup {backup_name}. Restoring...")
                
                # Perform rename and start defensively
                try:
                    backup.rename(name)
                    backup.start()
                    logger.info(f"Restored {name} from backup.")
                except Exception as e:
                    logger.error(f"Failed to rename or start backup container {backup_name}: {e}")
                    self.notifier.notify_rollback(name, False, f"Critical failure: Could not rename or start backup: {str(e)}")
                    return

            except docker.errors.NotFound:
                logger.error(f"Backup {backup_name} not found. Rollback impossible.")
                self.notifier.notify_rollback(name, False, "Rollback failed: Backup container not found.")
                return

            # 3. Final verification
            if self.health.wait_for_health(name, self.config.health_check_retries, self.config.health_check_delay):
                self.notifier.notify_rollback(name, True, "Restored from backup.")
            else:
                self.notifier.notify_rollback(name, False, "Container stopped or unhealthy after rollback attempt.")
        except Exception as e:
            logger.error(f"CRITICAL ROLLBACK FAILURE for {name}: {e}")
            self.notifier.notify_rollback(name, False, f"Unexpected error during rollback: {str(e)}")

    def restart_dependents(self, updated_containers: list[dict]) -> tuple[list[str], list[tuple[str, str]]]:
        """
        Restarts containers that explicitly depend on the updated containers via labels
        or NetworkMode.
        Returns (successfully_restarted_names, failed_restarts as (name, error) tuples).
        """
        if not updated_containers:
            return [], []

        restarted: list[str] = []
        failures: list[tuple[str, str]] = []

        updated_names = [u['name'] for u in updated_containers]
        updated_ids = [u['old_id'] for u in updated_containers]
        
        containers_to_restart = {}
        try:
            for c in self.client.containers.list():
                # Self-Protection already handled in discovery, but we double-check here
                labels = c.labels or {}
                if c.name in self.config.exclude_names or labels.get("watcher.self") == "true":
                    continue
                
                # Prevent restarting containers that were JUST updated in this same cycle
                if c.name in updated_names:
                    continue

                # 1. Label-based dependency
                depends_on = labels.get(self.config.depends_on_label_key, "")
                depends_list = [d.strip() for d in depends_on.split(",") if d.strip()]
                
                # 2. NetworkMode-based dependency
                net_mode = c.attrs.get('HostConfig', {}).get('NetworkMode', '')
                is_net_dependent = False
                if net_mode.startswith('container:'):
                    target = net_mode.split(':', 1)[1]
                    if target in updated_names:
                        is_net_dependent = True
                        logger.info(f"Network dependency (name) detected: {c.name} -> {target}")
                    elif target in updated_ids:
                        logger.warning(
                            f"UNSUPPORTED DEPENDENCY: {c.name} references {target[:12]} via ID. "
                            "This reference is now broken. Manual recreate of dependent container required."
                        )

                if is_net_dependent or any(u in depends_list for u in updated_names):
                    containers_to_restart[c.id] = c
                    
            for c in containers_to_restart.values():
                logger.info(f"Restarting dependent container {c.name}...")
                try:
                    c.restart(timeout=15)
                    logger.info(f"Successfully restarted dependent {c.name}.")
                    restarted.append(c.name)
                except Exception as e:
                    logger.error(f"Failed to restart dependent {c.name}: {e}")
                    failures.append((c.name, str(e)))
        except Exception as e:
            logger.error(f"Error checking dependents: {e}")

        return restarted, failures

    def run_cycle(self):
        logger.info("--- Cycle Start ---")
        auto_update, monitor_only = self.docker.get_watched_containers()
        summary = {"updated": [], "failed": [], "rolled_back": [], "reported": []}
        updated_info = [] # List of {'name': str, 'old_id': str}
        
        for c in auto_update:
            status, old_id = self.process_container(c, auto_update=True)
            if status == "updated":
                summary["updated"].append(c.name)
                updated_info.append({'name': c.name, 'old_id': old_id})
            elif status == "failed":
                summary["failed"].append(c.name)
            elif status == "rolled_back":
                summary["rolled_back"].append(c.name)
                
        for c in monitor_only:
            status, _ = self.process_container(c, auto_update=False)
            if status == "reported":
                summary["reported"].append(c.name)
            elif status == "failed":
                summary["failed"].append(c.name)
                
        if updated_info:
            restarted, dep_failures = self.restart_dependents(updated_info)
            self.notifier.notify_dependents_restarted(restarted, dep_failures)

        self.notifier.notify_summary_report(summary)
        logger.info("--- Cycle End ---")

    def start(self):
        logger.info("Watcher started.")
        self.notifier.notify_startup(self.config.check_interval, socket.gethostname())
        try:
            while True:
                self.run_cycle()
                time.sleep(self.config.check_interval)
        except KeyboardInterrupt:
            logger.info("Stopping...")
            self.notifier.notify_shutdown()
        except Exception as e:
            logger.critical(f"Crashed: {e}")

if __name__ == "__main__":
    WatcherService().start()
