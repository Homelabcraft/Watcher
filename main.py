import time
import logging
import docker
import sys
import signal
import threading
import socket
from datetime import datetime, timedelta
from typing import Optional

from config import Config
from notifier_factory import build_notifier
from docker_handler import DockerHandler
from health_monitor import HealthMonitor
from docker.models.containers import Container
from models import UpdateStatus, ContainerUpdateInfo, ExecutionPlan
from exceptions import ConfigurationError, RecreationError

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger('Watcher')

class WatcherService:
    def __init__(self):
        try:
            self.config = Config()
        except ConfigurationError as e:
            logger.critical(f"Startup failed due to configuration error: {e}")
            sys.exit(1)
            
        # Dynamically set logging level based on config
        log_level = getattr(logging, self.config.log_level, logging.INFO)
        logging.getLogger().setLevel(log_level)
        for handler in logging.getLogger().handlers:
            handler.setLevel(log_level)
            
        try:
            self.client = docker.from_env(timeout=120)
        except Exception as e:
            logger.critical(f"Docker connection failed: {e}")
            sys.exit(1)
            
        self.docker = DockerHandler(self.client, self.config)
        self.notifier = build_notifier(self.config)
        self.health = HealthMonitor(self.client)
        
        self.shutdown_event = threading.Event()
        self._setup_signals()

    def _setup_signals(self):
        """Setup graceful shutdown on SIGINT and SIGTERM."""
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Flags the service to stop safely after the current operation."""
        sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        logger.info(f"Received {sig_name}. Graceful shutdown initiated. Will exit after current cycle/update completes...")
        self.shutdown_event.set()

    def process_container(self, container: Container, auto_update: bool) -> ContainerUpdateInfo:
        """Standard production update lifecycle with rename backup protection."""
        name = container.name
        old_id = container.id
        old_image_id = container.image.id
        ref = self.docker.get_image_ref(container)
        recreate_started = False
        start_time = time.perf_counter()
        
        info = ContainerUpdateInfo(name=name, old_id=old_id, status=UpdateStatus.FAILED, image_ref=ref)

        try:
            # 1. Update Detection
            info.error_step = "update_check"
            status, old_img, new_img = self.docker.check_for_update(container)
            
            def shorten_hash(h):
                if not h: return None
                return h[7:19] if h.startswith("sha256:") else h[:12]
                
            info.old_image_short_id = shorten_hash(old_img)
            info.new_image_short_id = shorten_hash(new_img)
            
            if status == UpdateStatus.NO_UPDATE:
                info.status = UpdateStatus.NO_UPDATE
                info.error_step = None
                return info
            if status == UpdateStatus.FAILED:
                logger.error(f"Update check failed for {name}")
                info.error_message = "Update check failed"
                self.notifier.notify_update_failure(info)
                return info

            # Dry Run: Record UPDATE_AVAILABLE but stop execution
            if self.config.dry_run:
                logger.info(f"[DRY] Update available for {name} ({info.old_image_short_id} -> {info.new_image_short_id})")
                info.status = UpdateStatus.UPDATE_AVAILABLE
                info.error_step = None
                return info

            # Hybrid Mode: If not auto_update, we stop here and just report
            if not auto_update:
                logger.info(f"Update available for {name}, but auto-update is not enabled. Reporting only.")
                info.status = UpdateStatus.REPORTED
                info.error_step = None
                return info

            # 2. State Capture
            info.error_step = "state_capture"
            container.reload()
            current_status = container.status
            plan = self.docker.get_recreation_plan(container)
            dependents = self.get_dependents([name], [old_id])

            # 3. Execution
            info.error_step = "recreate"
            logger.info(f"Updating {name}...")
            self.notifier.notify_update_started(name, ref, info.old_image_short_id, info.new_image_short_id, current_status, dependents)
            
            recreate_started = True
            new_container = self.docker.recreate(name, plan)
            if new_container:
                info.new_id = new_container.id

            # 4. Verification
            info.error_step = "health_check"
            if self.health.wait_for_health(name, self.config.health_check_retries, self.config.health_check_delay):
                logger.info(f"Success: {name}")
                self.docker.remove_backup(name)
                
                # 5. Cleanup (Only after success)
                if self.config.cleanup_old_images:
                    self.docker.remove_image(old_image_id)
                
                info.status = UpdateStatus.UPDATED
                info.error_step = None
                info.duration_sec = time.perf_counter() - start_time
                self.notifier.notify_update_success(info)
                return info
            else:
                logger.error(f"Health failed: {name}. Rolling back...")
                info.error_message = "Unhealthy post-update"
                info.status = UpdateStatus.ROLLED_BACK
                info.error_step = "health_check"
                info.rollback_attempted = True
                
                # Do rollback
                rb_success, rb_detail = self.perform_rollback(name, old_image_id)
                info.rollback_success = rb_success
                info.rollback_details = rb_detail
                info.duration_sec = time.perf_counter() - start_time
                self.notifier.notify_update_failure(info)
                return info

        except RecreationError as e:
            logger.error(f"Recreation failed for {name}: {e}")
            info.error_message = str(e)
            
            if recreate_started:
                logger.info(f"Attempting rollback for {name} after recreation error...")
                info.rollback_attempted = True
                rb_success, rb_detail = self.perform_rollback(name, old_image_id)
                info.rollback_success = rb_success
                info.rollback_details = rb_detail
                
            info.duration_sec = time.perf_counter() - start_time
            self.notifier.notify_update_failure(info)
            return info
        except Exception as e:
            logger.error(f"Error processing {name}: {e}")
            info.error_message = str(e)
            
            if recreate_started:
                logger.info(f"Attempting rollback for {name} after execution error...")
                info.rollback_attempted = True
                rb_success, rb_detail = self.perform_rollback(name, old_image_id)
                info.rollback_success = rb_success
                info.rollback_details = rb_detail
                
            info.duration_sec = time.perf_counter() - start_time
            self.notifier.notify_update_failure(info)
            return info

    def perform_rollback(self, name: str, old_id: str) -> tuple[bool, str]:
        """Rollback using the saved backup container."""
        logger.warning(f"ROLLBACK for {name}")
        self.notifier.notify_rollback(name, "started")
        try:
            # 1. Inspect current container under target name
            try:
                current_container = self.client.containers.get(name)
                # Check image ID to see if update actually happened
                if current_container.image.id == old_id:
                    logger.info(f"Original container {name} is still in place (rename likely failed). Starting it...")
                    current_container.start()
                    msg = "Original container recovered (update didn't complete)."
                    self.notifier.notify_rollback(name, "success", msg)
                    return True, msg
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
                    msg = f"Critical failure: Could not rename or start backup: {str(e)}"
                    self.notifier.notify_rollback(name, "failed", msg)
                    return False, msg

            except docker.errors.NotFound:
                logger.error(f"Backup {backup_name} not found. Rollback impossible.")
                msg = "Rollback failed: Backup container not found."
                self.notifier.notify_rollback(name, "failed", msg)
                return False, msg

            # 3. Final verification
            if self.health.wait_for_health(name, self.config.health_check_retries, self.config.health_check_delay):
                msg = "Restored from backup and healthy."
                self.notifier.notify_rollback(name, "success", msg)
                return True, msg
            else:
                msg = "Container stopped or unhealthy after rollback attempt."
                self.notifier.notify_rollback(name, "failed", msg)
                return False, msg
        except Exception as e:
            logger.error(f"CRITICAL ROLLBACK FAILURE for {name}: {e}")
            msg = f"Unexpected error during rollback: {str(e)}"
            self.notifier.notify_rollback(name, "failed", msg)
            return False, msg

    def get_dependents(self, updated_names: list[str], updated_ids: list[str]) -> list[str]:
        """Finds names of containers that depend on the updated ones."""
        dependents = []
        try:
            for c in self.client.containers.list():
                labels = c.labels or {}
                if c.name in self.config.exclude_names or labels.get("watcher.self") == "true":
                    continue
                if c.name in updated_names:
                    continue
                    
                depends_on = labels.get(self.config.depends_on_label_key, "")
                depends_list = [d.strip() for d in depends_on.split(",") if d.strip()]
                
                net_mode = c.attrs.get('HostConfig', {}).get('NetworkMode', '')
                is_net_dependent = False
                if net_mode.startswith('container:'):
                    target = net_mode.split(':', 1)[1]
                    if target in updated_names:
                        is_net_dependent = True
                    elif target in updated_ids:
                        logger.warning(
                            f"UNSUPPORTED DEPENDENCY: {c.name} references {target[:12]} via ID. "
                            "This reference is now broken. Manual recreate of dependent container required."
                        )
                        
                if is_net_dependent or any(u in depends_list for u in updated_names):
                    dependents.append(c.name)
        except Exception as e:
            logger.error(f"Error finding dependents: {e}")
        return dependents

    def restart_dependents(self, updated_containers: list[ContainerUpdateInfo]):
        """
        Restarts containers that explicitly depend on the updated containers via labels
        or NetworkMode.
        """
        if not updated_containers:
            return
            
        updated_names = [u.name for u in updated_containers]
        updated_ids = [u.old_id for u in updated_containers]
        
        dependent_names = self.get_dependents(updated_names, updated_ids)
        if not dependent_names:
            return
            
        try:
            for c in self.client.containers.list():
                if c.name in dependent_names:
                    logger.info(f"Restarting dependent container {c.name}...")
                    try:
                        c.restart(timeout=15)
                        logger.info(f"Successfully restarted dependent {c.name}.")
                    except Exception as e:
                        logger.error(f"Failed to restart dependent {c.name}: {e}")
        except Exception as e:
            logger.error(f"Error checking dependents: {e}")

    def _get_sleep_duration(self) -> float:
        if not self.config.schedule_time:
            return float(self.config.check_interval)
            
        now = datetime.now()
        target_time = datetime.strptime(self.config.schedule_time, "%H:%M").time()
        target_dt = datetime.combine(now.date(), target_time)
        
        if now >= target_dt:
            target_dt += timedelta(days=1)
            
        return (target_dt - now).total_seconds()

    def run_cycle(self):
        cycle_start = time.perf_counter()
        logger.info("--- Cycle Start ---")
        auto_update, monitor_only = self.docker.get_watched_containers()
        
        total_checked = len(auto_update) + len(monitor_only)
        self.notifier.notify_scan_started(total_checked, "DRY_RUN" if self.config.dry_run else "LIVE")
        
        summary = {"updated": [], "failed": [], "rolled_back": [], "reported": []}
        updated_info = []
        all_infos = []
        
        plan = ExecutionPlan(checked_containers=total_checked)
        
        for c in auto_update:
            if self.shutdown_event.is_set():
                logger.info("Shutdown requested, skipping remaining auto-update containers in this cycle.")
                break
            info = self.process_container(c, auto_update=True)
            all_infos.append(info)
            if info.status == UpdateStatus.UPDATE_AVAILABLE:
                plan.updates_available.append(info)
            elif info.status == UpdateStatus.UPDATED:
                summary["updated"].append(info.name)
                updated_info.append(info)
            elif info.status == UpdateStatus.FAILED:
                summary["failed"].append(info.name)
            elif info.status == UpdateStatus.ROLLED_BACK:
                summary["rolled_back"].append(info.name)
                
        for c in monitor_only:
            if self.shutdown_event.is_set():
                logger.info("Shutdown requested, skipping remaining monitor-only containers in this cycle.")
                break
            info = self.process_container(c, auto_update=False)
            all_infos.append(info)
            if info.status in (UpdateStatus.REPORTED, UpdateStatus.UPDATE_AVAILABLE):
                summary["reported"].append(info.name)
                plan.monitored_only.append(info)
            elif info.status == UpdateStatus.FAILED:
                summary["failed"].append(info.name)
                
        if self.config.dry_run:
            if plan.updates_available:
                names = [u.name for u in plan.updates_available]
                ids = [u.old_id for u in plan.updates_available]
                plan.dependents_to_restart = self.get_dependents(names, ids)
            
            next_run = None
            if self.config.schedule_time:
                duration = self._get_sleep_duration()
                next_run = (datetime.now() + timedelta(seconds=duration)).strftime("%Y-%m-%d %H:%M")
                
            self.notifier.notify_execution_plan(plan, next_run)
            logger.info("--- Dry Run Cycle End ---")
            return
                
        if updated_info:
            self.restart_dependents(updated_info)
            
        cycle_duration = time.perf_counter() - cycle_start
        
        # Optionally hide "Updates Available" from summary report
        if not self.config.notify_updates_available:
            summary["reported"] = []
            
        self.notifier.notify_summary_report(summary, all_infos, duration_sec=cycle_duration)
        logger.info("--- Cycle End ---")

    def start(self):
        logger.info("Watcher started.")
        config_dict = {
            "check_interval": self.config.check_interval,
            "schedule_time": self.config.schedule_time,
            "dry_run": self.config.dry_run,
            "watch_by_label": self.config.watch_by_label,
            "cleanup_old_images": self.config.cleanup_old_images,
            "health_check_retries": self.config.health_check_retries,
            "health_check_delay": self.config.health_check_delay
        }
        self.notifier.notify_startup(config_dict, socket.gethostname())
        try:
            while not self.shutdown_event.is_set():
                self.run_cycle()
                
                if self.shutdown_event.is_set():
                    break
                
                sleep_sec = self._get_sleep_duration()
                if self.config.schedule_time:
                    next_run = (datetime.now() + timedelta(seconds=sleep_sec)).strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"Sleeping until next scheduled run at {next_run}...")
                
                self.shutdown_event.wait(sleep_sec)
                    
        except Exception as e:
            logger.critical(f"Crashed: {e}")
        finally:
            logger.info("Watcher stopped securely.")
            self.notifier.notify_shutdown()

if __name__ == "__main__":
    WatcherService().start()
