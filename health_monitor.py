import logging
import time
import docker

logger = logging.getLogger('Watcher.Health')

class HealthMonitor:
    """Verifies container stability using Docker status and healthchecks."""
    def __init__(self, client: docker.DockerClient):
        self.client = client

    def is_healthy(self, name: str) -> bool:
        """Single check of the current health state."""
        try:
            c = self.client.containers.get(name)
            c.reload()
            state = c.attrs.get('State', {})
            status = state.get('Status', '')
            health = state.get('Health', {}).get('Status', 'none')

            # Container must be running
            if status != 'running': 
                return False
            
            # If Docker healthcheck exists, it must not be unhealthy or starting
            if health in ('unhealthy', 'starting'): 
                return False
            
            return True
        except:
            return False

    def wait_for_health(self, name: str, retries: int, delay: int) -> bool:
        """
        Polls for health status. Requires stability (multiple successes)
        if enough retries are provided.
        """
        required_successes = min(2, retries) if retries > 0 else 1
        consecutive_successes = 0
        
        logger.info(f"Verifying health for {name} (Stability: {required_successes} successful checks required)...")
        
        # Initial wait for container startup
        time.sleep(2)

        for i in range(max(1, retries)):
            if self.is_healthy(name):
                consecutive_successes += 1
                if consecutive_successes >= required_successes:
                    logger.info(f"Container {name} is stable and healthy.")
                    return True
                logger.debug(f"Stability check {consecutive_successes}/{required_successes} passed.")
            else:
                consecutive_successes = 0
                logger.info(f"Check {i+1}/{retries} for {name} failed or starting. Retrying in {delay}s...")
            
            time.sleep(delay)
            
        return False
