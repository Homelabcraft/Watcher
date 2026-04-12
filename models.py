from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List

class UpdateStatus(Enum):
    NO_UPDATE = auto()
    UPDATE_AVAILABLE = auto()  # Used in Dry Run when an update is found
    UPDATED = auto()
    ROLLED_BACK = auto()
    FAILED = auto()
    REPORTED = auto()
    SKIPPED_COOLDOWN = auto()

@dataclass
class ContainerUpdateInfo:
    name: str
    old_id: str
    status: UpdateStatus
    error_message: Optional[str] = None
    error_step: Optional[str] = None
    old_image_short_id: Optional[str] = None
    new_image_short_id: Optional[str] = None
    image_ref: Optional[str] = None
    new_id: Optional[str] = None
    duration_sec: float = 0.0
    rollback_attempted: bool = False
    rollback_success: bool = False
    rollback_details: Optional[str] = None
    container_running_post_error: bool = False

@dataclass
class ExecutionPlan:
    checked_containers: int = 0
    updates_available: List[ContainerUpdateInfo] = field(default_factory=list)
    monitored_only: List[ContainerUpdateInfo] = field(default_factory=list)
    dependents_to_restart: List[str] = field(default_factory=list)

