import logging
import docker
import docker.errors
import socket
from docker.types import Mount
from docker.models.containers import Container
from typing import Optional, List

logger = logging.getLogger('Watcher.Docker')

class DockerHandler:
    """Hardened Docker API handler with SDK compliance and self-protection."""
    def __init__(self, client: docker.DockerClient, config=None):
        self.client = client
        self.config = config
        self.dry_run = config.dry_run if config else False
        self.self_id = self._detect_self_id()

    def _detect_self_id(self) -> Optional[str]:
        try:
            hostname = socket.gethostname()
            c = self.client.containers.get(hostname)
            return c.id
        except:
            return None

    def get_watched_containers(self) -> tuple[List[Container], List[Container]]:
        """Finds containers using internal config for filtering. Returns (auto_update, monitor_only)."""
        auto_update = []
        monitor_only = []
        try:
            for c in self.client.containers.list():
                labels = c.labels or {}
                if self.self_id and c.id == self.self_id: continue
                if self.config and c.name in self.config.exclude_names: continue
                if labels.get("watcher.self") == "true": continue

                tags = c.image.tags
                if not any(t.endswith(':latest') for t in tags): continue
                
                enable_label = labels.get(self.config.watch_label_key)
                
                if self.config and self.config.watch_by_label:
                    if enable_label == self.config.watch_label_value:
                        auto_update.append(c)
                    else:
                        monitor_only.append(c)
                else:
                    if enable_label == "false":
                        monitor_only.append(c)
                    else:
                        auto_update.append(c)

            return auto_update, monitor_only
        except Exception as e:
            logger.error(f"Error listing containers: {e}")
            return [], []

    def get_image_ref(self, container: Container) -> Optional[str]:
        for t in container.image.tags:
            if t.endswith(':latest'): return t
        return None

    def check_for_update(self, container: Container) -> str:
        if self.dry_run: return "skipped_dry_run"
        ref = self.get_image_ref(container)
        if not ref: return "error"
        try:
            auth = None
            if self.config and self.config.reg_user:
                auth = {"username": self.config.reg_user, "password": self.config.reg_pass}
            
            old_id = container.image.id
            self.client.images.pull(ref, auth_config=auth)
            new_image = self.client.images.get(ref)
            return "update_available" if old_id != new_image.id else "no_update"
        except Exception as e:
            logger.error(f"Pull failed for {container.name}: {e}")
            return "error"

    def get_recreation_plan(self, container: Container) -> dict:
        container.reload()
        attrs = container.attrs
        config = attrs.get('Config', {})
        host_config = attrs.get('HostConfig', {})
        
        # 1. Ports: Multiple bindings and HostIP support + Integer conversion
        ports = {}
        pb = host_config.get('PortBindings')
        if pb:
            for c_port, h_list in pb.items():
                bindings = []
                for h_bind in h_list:
                    h_port = h_bind.get('HostPort')
                    h_ip = h_bind.get('HostIp')
                    try:
                        normalized_port = int(h_port) if h_port else None
                    except (ValueError, TypeError):
                        normalized_port = h_port
                    
                    if h_ip:
                        bindings.append((h_ip, normalized_port))
                    else:
                        bindings.append(normalized_port)
                ports[c_port] = bindings if len(bindings) > 1 else (bindings[0] if bindings else None)

        # 2. Mounts: Use Mount objects, skip anonymous
        mount_objects = []
        for m in (attrs.get('Mounts') or []):
            m_type = m.get('Type')
            if m_type not in ('bind', 'volume'): continue
            target = m.get('Destination')
            source = m.get('Source') if m_type == 'bind' else m.get('Name')
            if m_type == 'volume' and source and len(source) == 64 and all(c in '0123456789abcdef' for c in source.lower()):
                continue
            mount_objects.append(Mount(target=target, source=source, type=m_type, read_only=not m.get('RW', True)))

        # 3. Healthcheck: Explicit SDK mapping
        hc_orig = config.get('Healthcheck', {})
        healthcheck = None
        if hc_orig:
            mapping = {'Test': 'test', 'Interval': 'interval', 'Timeout': 'timeout', 'Retries': 'retries', 'StartPeriod': 'start_period'}
            healthcheck = {mapping[k]: v for k, v in hc_orig.items() if k in mapping}

        # 4. Restart Policy
        rp = host_config.get('RestartPolicy', {})
        restart_policy = {"Name": rp.get('Name')} if rp.get('Name') else None
        if restart_policy and "MaximumRetryCount" in rp:
            restart_policy["MaximumRetryCount"] = rp["MaximumRetryCount"]

        return {
            "create_args": {
                "name": container.name,
                "image": self.get_image_ref(container),
                "hostname": config.get('Hostname'),
                "user": config.get('User'),
                "working_dir": config.get('WorkingDir'),
                "command": config.get('Cmd'),
                "entrypoint": config.get('Entrypoint'),
                "environment": config.get('Env'),
                "labels": config.get('Labels'),
                "ports": ports,
                "mounts": mount_objects,
                "restart_policy": restart_policy,
                "network_mode": host_config.get('NetworkMode'),
                "privileged": host_config.get('Privileged'),
                "read_only": host_config.get('ReadonlyRootfs', False),
                "cap_add": host_config.get('CapAdd'),
                "cap_drop": host_config.get('CapDrop'),
                "devices": [f"{d['PathOnHost']}:{d['PathInContainer']}:{d.get('CgroupPermissions', 'rwm')}" for d in (host_config.get('Devices') or []) if 'PathOnHost' in d],
                "extra_hosts": {eh.split(':', 1)[0]: eh.split(':', 1)[1] for eh in (host_config.get('ExtraHosts') or []) if ':' in eh},
                "dns": host_config.get('Dns'),
                "healthcheck": healthcheck,
                "detach": True
            },
            "networks": attrs.get('NetworkSettings', {}).get('Networks') or {}
        }

    def recreate(self, name: str, plan: dict):
        if self.dry_run: return None
        ca = plan["create_args"]
        nets = plan["networks"]
        
        # Networking Configuration for create()
        networking_config = None
        primary_net_name = None
        if nets and not str(ca.get('network_mode')).startswith('container:'):
            primary_net_name = list(nets.keys())[0]
            n_cfg = nets[primary_net_name]
            networking_config = self.client.api.create_networking_config({
                primary_net_name: self.client.api.create_endpoint_config(
                    aliases=n_cfg.get('Aliases'),
                    ipv4_address=n_cfg.get('IPAddress') if n_cfg.get('IPAddress') else None
                )
            })
            if ca.get('network_mode') == primary_net_name:
                ca.pop('network_mode')

        try:
            try:
                old = self.client.containers.get(name)
                old.stop(timeout=15)
                backup_name = f"{name}_backup"
                try:
                    existing = self.client.containers.get(backup_name)
                    existing.remove(force=True)
                except docker.errors.NotFound: pass
                old.rename(backup_name)
            except docker.errors.NotFound: pass

            logger.info(f"Creating {name}...")
            new = self.client.containers.create(networking_config=networking_config, **ca)
            
            # Additional networks
            for net_name, net_config in nets.items():
                if net_name == primary_net_name: continue
                try:
                    network = self.client.networks.get(net_name)
                    network.reload()
                    
                    if any(c.id == new.id for c in network.containers):
                        continue

                    network.connect(new, aliases=net_config.get('Aliases'), 
                                    ipv4_address=net_config.get('IPAddress') if net_config.get('IPAddress') else None)
                except docker.errors.APIError as e:
                    logger.warning(f"Net-Connect API error for {net_name}: {e}")
                except Exception as e:
                    logger.warning(f"Net-Connect error for {net_name}: {e}")
            
            new.start()
            return new
        except Exception as e:
            logger.error(f"RECREATION FAILED for {name}: {e}")
            raise

    def remove_backup(self, name: str):
        """Removes the backup container after a successful update."""
        backup_name = f"{name}_backup"
        try:
            backup = self.client.containers.get(backup_name)
            backup.remove(force=True)
            logger.info(f"Removed backup container {backup_name}")
        except docker.errors.NotFound:
            pass
        except Exception as e:
            logger.warning(f"Failed to remove backup {backup_name}: {e}")

    def remove_image(self, image_id: str):
        """Removes an old image if possible."""
        try:
            logger.info(f"Cleaning up image {image_id[:12]}...")
            self.client.images.remove(image=image_id, noprune=False)
        except Exception as e:
            logger.debug(f"Image cleanup skipped: {e}")
