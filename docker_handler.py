import logging
import re
import socket

import docker
import docker.errors
import requests
from docker.models.containers import Container
from docker.types import LogConfig, Mount, Ulimit

from exceptions import RecreationError
from models import UpdateStatus

logger = logging.getLogger('Watcher.Docker')

class DockerHandler:
    """Hardened Docker API handler with SDK compliance and self-protection."""
    def __init__(self, client: docker.DockerClient, config=None):
        self.client = client
        self.config = config
        self.dry_run = config.dry_run if config else False
        self.self_id = self._detect_self_id()

    def _detect_self_id(self) -> str | None:
        try:
            hostname = socket.gethostname()
            c = self.client.containers.get(hostname)
            return c.id
        except Exception:  # noqa: BLE001
            return None

    def get_watched_containers(self) -> tuple[list[Container], list[Container]]:
        """Finds containers using internal config for filtering. Returns (auto_update, monitor_only)."""
        auto_update = []
        monitor_only = []

        # Pre-compile regex for performance
        exclude_re = None
        if self.config and self.config.exclude_regex:
            try:
                exclude_re = re.compile(self.config.exclude_regex)
            except Exception:  # noqa: BLE001
                logger.warning(f"Failed to compile exclude regex: {self.config.exclude_regex}")

        try:
            for c in self.client.containers.list():
                labels = c.labels or {}

                # Critical Self-Protection: Skip our own container by ID, name, or specific label
                if self.self_id and c.id == self.self_id: continue
                if labels.get("watcher.self") == "true": continue
                if self.config:
                    if c.name in self.config.exclude_names: continue
                    if exclude_re and exclude_re.search(c.name): continue

                # Evaluate image reference via Config.Image or RepoTags
                ref = self.get_image_ref(c)
                if not ref:
                    continue

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
            raise

    def get_image_ref(self, container: Container) -> str | None:
        """
        Determines the relevant image reference for a container.
        Returns only the exact reference used during container creation (Config.Image) if it ends with ':latest'.
        """
        config_image = container.attrs.get('Config', {}).get('Image', '')
        if config_image.endswith(':latest'):
            return config_image

        return None

    def check_for_update(self, container: Container) -> tuple[UpdateStatus, str | None, str | None]:
        ref = self.get_image_ref(container)
        if not ref: return UpdateStatus.FAILED, None, None
        try:
            auth = None
            if self.config and self.config.reg_user:
                auth = {"username": self.config.reg_user, "password": self.config.reg_pass}

            old_id = container.image.id
            self.client.images.pull(ref, auth_config=auth)
            new_image = self.client.images.get(ref)

            if old_id != new_image.id:
                return UpdateStatus.UPDATE_AVAILABLE, old_id, new_image.id
            else:
                return UpdateStatus.NO_UPDATE, old_id, old_id
        except docker.errors.APIError as e:
            logger.error(f"Docker API error during pull for {container.name}: {e}")
            return UpdateStatus.FAILED, None, None
        except Exception as e:  # noqa: BLE001
            logger.error(f"Unexpected pull error for {container.name}: {e}")
            return UpdateStatus.FAILED, None, None

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

        # 2. Mounts: Use Mount objects
        mount_objects = []
        for m in (attrs.get('Mounts') or []):
            m_type = m.get('Type')
            if m_type not in ('bind', 'volume'): continue
            target = m.get('Destination')
            source = m.get('Source') if m_type == 'bind' else m.get('Name')
            # Propagation is important for shared mounts
            propagation = m.get('Propagation') if m_type == 'bind' else None

            # Anonymous volumes (64 hex chars) are kept as their name is valid and ensures data retention
            mount_objects.append(Mount(
                target=target,
                source=source,
                type=m_type,
                read_only=not m.get('RW', True),
                propagation=propagation
            ))

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

        # 5. Ulimits
        ulimits = []
        for ul in (host_config.get('Ulimits') or []):
            ulimits.append(Ulimit(name=ul['Name'], soft=ul['Soft'], hard=ul['Hard']))

        # 6. LogConfig
        lc_raw = host_config.get('LogConfig', {})
        log_config = LogConfig(type=lc_raw.get('Type'), config=lc_raw.get('Config')) if lc_raw.get('Type') else None

        # 7. Device Requests
        device_reqs = []
        for d in (host_config.get('DeviceRequests') or []):
            device_reqs.append(docker.types.DeviceRequest(
                driver=d.get('Driver'),
                count=d.get('Count'),
                device_ids=d.get('DeviceIDs'),
                capabilities=d.get('Capabilities'),
                options=d.get('Options')
            ))

        create_args = {
            "name": container.name,
            "image": self.get_image_ref(container),
            "hostname": config.get('Hostname'),
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
            "mem_limit": host_config.get('Memory'),
            "mem_reservation": host_config.get('MemoryReservation'),
            "memswap_limit": host_config.get('MemorySwap'),
            "cpu_shares": host_config.get('CpuShares'),
            "cpu_quota": host_config.get('CpuQuota'),
            "cpu_period": host_config.get('CpuPeriod'),
            "cpuset_cpus": host_config.get('CpusetCpus'),
            "pids_limit": host_config.get('PidsLimit'),
            "stop_signal": config.get('StopSignal'),
            "stop_timeout": config.get('StopTimeout'),
            "tty": config.get('Tty'),
            "stdin_open": config.get('OpenStdin'),
            "security_opt": host_config.get('SecurityOpt'),
            "group_add": host_config.get('GroupAdd'),
            "tmpfs": host_config.get('Tmpfs'),
            "devices": [f"{d['PathOnHost']}:{d['PathInContainer']}:{d.get('CgroupPermissions', 'rwm')}" for d in (host_config.get('Devices') or []) if 'PathOnHost' in d],
            "device_requests": device_reqs or None,
            "extra_hosts": {eh.split(':', 1)[0]: eh.split(':', 1)[1] for eh in (host_config.get('ExtraHosts') or []) if ':' in eh},
            "dns": host_config.get('Dns'),
            "dns_search": host_config.get('DnsSearch'),
            "dns_opt": host_config.get('DnsOptions'),
            "healthcheck": healthcheck,
            "sysctls": host_config.get('Sysctls'),
            "ulimits": ulimits,
            "log_config": log_config,
            "shm_size": host_config.get('ShmSize'),
            "ipc_mode": host_config.get('IpcMode'),
            "pid_mode": host_config.get('PidMode'),
            "detach": True
        }

        # User handling: Only set if truthy to avoid "unable to find user" errors
        # if the image has changed or the value is explicitly empty.
        user = config.get('User')
        if user:
            create_args["user"] = user

        # Cleanup: Remove None values to avoid SDK issues
        create_args = {k: v for k, v in create_args.items() if v is not None}

        nets = attrs.get('NetworkSettings', {}).get('Networks') or {}

        # Abort if explicit static IP is configured
        for net_name, net_info in nets.items():
            ipam = net_info.get('IPAMConfig') or {}
            if ipam.get('IPv4Address') or ipam.get('IPv6Address'):
                raise RecreationError(f"Container {container.name} uses a static IP on network {net_name}. Static-IP recreation is unsupported and must be handled manually.")

        return {
            "create_args": create_args,
            "networks": nets
        }



    def recreate(self, name: str, plan: dict, state_store=None, transaction_id: str=None) -> Container | None:  # noqa: RUF013
        if self.dry_run: return None
        ca = plan["create_args"].copy() # Work on a copy to allow retry modifications

        # Apply transaction ID label
        labels = dict(ca.get("labels") or {})
        labels.pop("watcher.transaction_id", None)
        if transaction_id:
            labels["watcher.transaction_id"] = transaction_id
        ca["labels"] = labels

        nets = plan["networks"]

        # Networking Configuration for create()
        networking_config = None
        primary_net_name = None

        network_mode = str(ca.get('network_mode', ''))
        is_special_mode = network_mode in ('host', 'none', 'default', 'bridge') or network_mode.startswith('container:')

        # Defensive check for networks to avoid IndexError
        if nets and not is_special_mode:
            keys = list(nets.keys())
            if keys:
                primary_net_name = keys[0]
                n_cfg = nets[primary_net_name]
                networking_config = self.client.api.create_networking_config({
                    primary_net_name: self.client.api.create_endpoint_config(
                        aliases=n_cfg.get('Aliases')
                    )
                })
                if network_mode == primary_net_name:
                    ca.pop('network_mode', None)

        # 1. State Capture: Stop and Rename old container
        try:
            old = self.client.containers.get(name)
        except docker.errors.NotFound:
            raise RecreationError(f"Original container {name} not found. Cannot proceed with recreation.")

        backup_name = f"{name}_backup"
        # PRE-CHECK: Check for backup existence BEFORE stopping main container
        try:
            self.client.containers.get(backup_name)
            backup_exists = True
        except docker.errors.NotFound:
            backup_exists = False

        if backup_exists:
            raise RecreationError(f"Backup container {backup_name} already exists. Aborting update for safety. Please resolve manually or restart Watcher for auto-recovery.")

        try:

            # Use container's specific stop timeout if defined, otherwise 15s
            stop_timeout = old.attrs.get('Config', {}).get('StopTimeout')
            timeout = int(stop_timeout) if stop_timeout is not None else 15

            logger.info(f"Stopping original container {name} (timeout: {timeout}s)...")
            try:
                old.stop(timeout=timeout)
            except requests.exceptions.ReadTimeout:
                logger.warning(f"Stop request for {name} timed out in python client. Assuming Docker daemon is still stopping it.")
                import time
                for _ in range(15):
                    old.reload()
                    if old.status != "running":
                        break
                    time.sleep(2)

                old.reload()
                if old.status == "running":
                    raise RecreationError(f"Container {name} failed to stop after timeout. Refusing to rename or replace it.")

            old.rename(backup_name)
            if state_store:
                state_store.update_transaction(name, "backup_renamed", backup_container_id=old.id)
        except Exception as e:  # noqa: BLE001
            raise RecreationError(f"Failed to stop/rename original container {name}: {e}")

        # 2. Execution: Attempt recreation (with potential retry for User errors)
        attempts = 0
        max_attempts = 2

        while attempts < max_attempts:
            attempts += 1
            new_container = None
            try:
                logger.info(f"Creating {name} (Attempt {attempts}/{max_attempts})...")
                new_container = self.client.containers.create(networking_config=networking_config, **ca)
                if state_store:
                    try:
                        state_store.update_transaction(name, "replacement_created", new_container_id=new_container.id, new_image_id=new_container.image.id)
                    except Exception as state_e:  # noqa: BLE001
                        logger.error(f"Failed to persist replacement_created state for {name}: {state_e}. Removing untracked new container.")
                        try:
                            new_container.remove(force=True)
                        except Exception as rem_e:  # noqa: BLE001
                            logger.error(f"Failed to remove untracked container {new_container.id}: {rem_e}")
                        raise RecreationError(f"State persistence failed after container creation: {state_e}")

                # Additional networks
                for net_name, net_config in nets.items():
                    if net_name == primary_net_name or is_special_mode: continue
                    try:
                        network = self.client.networks.get(net_name)
                        network.reload()
                        if not any(c.id == new_container.id for c in network.containers):
                            network.connect(new_container, aliases=net_config.get('Aliases'))
                    except Exception as net_e:  # noqa: BLE001
                        logger.error(f"Net-Connect error for {net_name}: {net_e}")
                        if new_container:
                            try:
                                new_container.remove(force=True)
                            except Exception as e_remove: # noqa: BLE001
                                logger.error(f"Failed to remove container {new_container.id}: {e_remove}")
                        raise RecreationError(f"Failed to connect secondary network {net_name}: {net_e}")

                new_container.start()
                if state_store:
                    state_store.update_transaction(name, "replacement_started")
                return new_container

            except docker.errors.APIError as e:
                error_msg = str(e).lower()
                is_user_error = "unable to find user" in error_msg or "no matching entries in passwd file" in error_msg

                # If it's a user resolution error and we haven't tried without user yet
                if is_user_error and "user" in ca and attempts < max_attempts:
                    if not getattr(self.config, 'allow_user_fallback', False):
                        logger.error(f"RECREATION FAILED for {name}: User '{ca['user']}' not found in new image. Fallback to root disabled.")
                        raise RecreationError(f"User '{ca['user']}' not found in new image. Update aborted for safety.")

                    logger.warning(
                        f"RECREATION FAILED for {name} due to User configuration ('{ca['user']}'). "
                        "The new image might not have this user. Retrying WITHOUT user..."
                    )
                    # Cleanup the failed new container before retry
                    if new_container:
                        try:
                            new_container.remove(force=True)
                        except Exception as e_remove: # noqa: BLE001
                            logger.error(f"Failed to remove new container: {e_remove}")

                    ca.pop("user")
                    continue

                logger.error(f"Docker API Error during recreation of {name} on attempt {attempts}: {e}")
                raise RecreationError(f"Docker API Error: {e}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Unexpected Error during recreation of {name} on attempt {attempts}: {e}")
                raise RecreationError(f"Unexpected Error: {e}")

        raise RecreationError(f"Failed to recreate {name} after {max_attempts} attempts.")

    def remove_backup(self, name: str) -> bool:
        """Removes the backup container after a successful update."""
        if self.dry_run: return True
        backup_name = f"{name}_backup"
        try:
            backup = self.client.containers.get(backup_name)
            backup.remove(force=True)
            logger.info(f"Removed backup container {backup_name}")
            return True
        except docker.errors.NotFound:
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to remove backup {backup_name}: {e}")
            return False

    def remove_image(self, image_id: str):
        """Removes an old image if possible."""
        try:
            logger.info(f"Cleaning up image {image_id[:12]}...")
            self.client.images.remove(image=image_id, noprune=False)
        except docker.errors.APIError as e:
            logger.debug(f"Image cleanup skipped (in use or missing): {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Unexpected error during image cleanup: {e}")
