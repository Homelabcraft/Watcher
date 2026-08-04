from dataclasses import dataclass, field
from enum import Enum, auto


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
    error_message: str | None = None
    error_step: str | None = None
    old_image_short_id: str | None = None
    new_image_short_id: str | None = None
    image_ref: str | None = None
    new_id: str | None = None
    duration_sec: float = 0.0
    rollback_attempted: bool = False
    rollback_success: bool = False
    rollback_details: str | None = None

@dataclass
class ExecutionPlan:
    checked_containers: int = 0
    updates_available: list[ContainerUpdateInfo] = field(default_factory=list)
    monitored_only: list[ContainerUpdateInfo] = field(default_factory=list)
    dependents_to_restart: list[str] = field(default_factory=list)

