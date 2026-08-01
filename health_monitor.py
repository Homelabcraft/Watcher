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
        try:
            c = self.client.containers.get(name)
            labels = c.labels or {}
            start_period = labels.get('watcher.health.start_period')
            if start_period:
                try:
                    sp_sec = int(start_period)
                    logger.info(f"Container {name} has start_period={sp_sec}s label. Waiting...")
                    time.sleep(sp_sec)
                except ValueError:
                    pass
            
            if c.attrs.get('State', {}).get('Health', {}).get('Status', 'none') == 'none':
                logger.warning(f"Container {name} has no native Docker healthcheck. Stability check will only verify 'running' state.")
        except: pass

        # Initial wait for container startup if no custom start period handled it
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
