import logging
import time
import docker

logger = logging.getLogger('Watcher.Health')

class HealthMonitor:
    """Verifies container stability using Docker status and healthchecks."""
    def __init__(self, client: docker.DockerClient, shutdown_event=None):
        self.client = client
        self.shutdown_event = shutdown_event

    def _sleep(self, seconds: float) -> bool:
        """Interruptible sleep. Returns False if shutdown was requested during sleep."""
        if self.shutdown_event:
            return not self.shutdown_event.wait(seconds)
        time.sleep(seconds)
        return True

    def is_healthy(self, name: str) -> bool:
        """Single check of the current health state."""
        try:
            c = self.client.containers.get(name)
            c.reload()
            state = c.attrs.get('State', {})
            status = state.get('Status', '')
            health_data = state.get('Health', {})
            health = health_data.get('Status', 'none')

            # Container must be running
            if status != 'running': 
                return False
            
            # If Docker healthcheck exists, it must not be unhealthy or starting
            if health != 'none':
                if health in ('unhealthy', 'starting'): 
                    return False
            
            return True
        except docker.errors.NotFound:
            return False
        except Exception as e:
            logger.debug(f"Error checking health state for {name}: {e}")
            return False

    def wait_for_health(self, name: str, retries: int, delay: int) -> bool:
        """
        Polls for health status. Requires stability (multiple successes)
        if enough retries are provided.
        """
        required_successes = min(2, retries) if retries > 0 else 1
        consecutive_successes = 0
        
        logger.info(f"Verifying health for {name} (Stability: {required_successes} successful checks required)...")
        
        # Check if container has a native healthcheck and start_period
        start_period_applied = False
        try:
            c = self.client.containers.get(name)
            labels = c.labels or {}
            start_period = labels.get('watcher.health.start_period')
            if start_period:
                try:
                    sp_sec = int(start_period)
                    if sp_sec < 0:
                        logger.warning(f"Invalid start_period {sp_sec} for {name}. Must be >= 0.")
                    else:
                        logger.info(f"Container {name} has start_period={sp_sec}s label. Waiting...")
                        if not self._sleep(sp_sec): return False
                        start_period_applied = True
                except ValueError:
                    logger.warning(f"Invalid start_period '{start_period}' for {name}. Must be an integer >= 0.")
            
            if c.attrs.get('State', {}).get('Health', {}).get('Status', 'none') == 'none':
                logger.warning(f"Container {name} has no native Docker healthcheck. Stability check will only verify 'running' state.")
        except Exception as e:
            logger.debug(f"Failed to inspect health details for {name}: {e}")

        # Initial wait for container startup if no custom start period handled it
        if not start_period_applied:
            if not self._sleep(2): return False

        max_retries = max(1, retries)
        for i in range(max_retries):
            if self.is_healthy(name):
                consecutive_successes += 1
                if consecutive_successes >= required_successes:
                    logger.info(f"Container {name} is stable and healthy.")
                    return True
                logger.debug(f"Stability check {consecutive_successes}/{required_successes} passed.")
            else:
                consecutive_successes = 0
                logger.info(f"Check {i+1}/{max_retries} for {name} failed or starting.")
                if i < max_retries - 1:
                    logger.info(f"Retrying in {delay}s...")
            
            if i < max_retries - 1:
                if not self._sleep(delay): return False
            
        return False
