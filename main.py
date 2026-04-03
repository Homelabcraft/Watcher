import time
import logging
import docker
import sys
import copy

from config import Config
from discord_notifier import DiscordNotifier
from docker_handler import DockerHandler, RollbackContext
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

    def process_container(self, container: Container):
        """Standard production update lifecycle with deepcopy protection."""
        name = container.name
        old_id = container.image.id
        ref = self.docker.get_image_ref(container)
        rb_ctx = None
        recreate_started = False
        
        try:
            # 1. Update Detection
            status = self.docker.check_for_update(container)
            if status == "no_update": return
            if status == "skipped_dry_run":
                logger.info(f"[DRY] Would check updates for {name}")
                return
            if status == "error":
                self.notifier.notify_failure(name, "Update check failed.")
                return

            # 2. State Capture (Critical: use deepcopy for rollback context)
            container.reload()
            new_id = self.client.images.get(ref).id
            plan = self.docker.get_recreation_plan(container)
            rb_ctx = RollbackContext(name, old_id, ref, copy.deepcopy(plan))
            
            self.notifier.notify_update(name, ref, old_id, new_id)

            # 3. Execution
            logger.info(f"Updating {name}...")
            self.notifier.notify_recreation_started(name)
            
            recreate_started = True
            self.docker.recreate(name, plan)

            # 4. Verification
            if self.health.wait_for_health(name, self.config.health_check_retries, self.config.health_check_delay):
                logger.info(f"Success: {name}")
                self.notifier.notify_success(name, ref)
                
                # 5. Cleanup (Only after success)
                if self.config.cleanup_old_images:
                    self.docker.remove_image(old_id)
            else:
                logger.error(f"Health failed: {name}. Rolling back...")
                self.notifier.notify_failure(name, "Unhealthy post-update.")
                self.perform_rollback(rb_ctx)

        except Exception as e:
            logger.error(f"Error processing {name}: {e}")
            self.notifier.notify_failure(name, f"Unexpected error: {str(e)}")
            if rb_ctx and recreate_started:
                logger.info(f"Attempting rollback for {name} after execution error...")
                self.perform_rollback(rb_ctx)

    def perform_rollback(self, ctx: RollbackContext):
        """Rollback using bit-perfect Image ID and deepcopied plan."""
        logger.warning(f"ROLLBACK for {ctx.name} to {ctx.old_image_id[:12]}")
        try:
            # Ensure we work on a copy to prevent side effects
            rb_plan = copy.deepcopy(ctx.plan)
            rb_plan["create_args"]["image"] = ctx.old_image_id
            
            self.docker.recreate(ctx.name, rb_plan)
            
            if self.health.wait_for_health(ctx.name, self.config.health_check_retries, self.config.health_check_delay):
                self.notifier.notify_rollback(ctx.name, True, f"Recovered image `{ctx.old_image_id[:12]}`.")
            else:
                self.notifier.notify_rollback(ctx.name, False, "Container stopped or unhealthy after rollback attempt.")
        except Exception as e:
            logger.error(f"CRITICAL ROLLBACK FAILURE: {e}")
            self.notifier.notify_rollback(ctx.name, False, f"Critical error during rollback: {str(e)}")

    def run_cycle(self):
        logger.info("--- Cycle Start ---")
        watched = self.docker.get_watched_containers()
        for c in watched:
            self.process_container(c)
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
