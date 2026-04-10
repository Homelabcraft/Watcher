from config import Config
from discord_notifier import DiscordNotifier
from multi_notifier import MultiNotifier
from noop_notifier import NoopNotifier
from notifier_protocol import Notifier


def build_notifier(config: Config) -> Notifier:
    """Compose all configured notification backends."""
    backends: list[Notifier] = []
    if getattr(config, "discord_webhook_url", None):
        backends.append(DiscordNotifier(config.discord_webhook_url))
    if not backends:
        return NoopNotifier()
    if len(backends) == 1:
        return backends[0]
    return MultiNotifier(backends)
