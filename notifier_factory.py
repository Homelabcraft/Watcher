import logging

from config import Config
from discord_notifier import DiscordNotifier
from multi_notifier import MultiNotifier
from noop_notifier import NoopNotifier
from notifier_protocol import Notifier
from ntfy_notifier import NtfyNotifier
from slack_notifier import SlackNotifier
from telegram_notifier import TelegramNotifier

logger = logging.getLogger('Watcher.NotifierFactory')

def build_notifier(config: Config) -> Notifier:
    """Compose all configured notification backends."""
    backends: list[Notifier] = []
    
    if config.discord_webhook_url:
        backends.append(DiscordNotifier(config.discord_webhook_url))
        
    if config.slack_webhook_url:
        backends.append(SlackNotifier(config.slack_webhook_url))
        
    if config.ntfy_url:
        backends.append(NtfyNotifier(config.ntfy_url))
        
    if config.telegram_bot_token and config.telegram_chat_id:
        backends.append(TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id))
        
    if not backends:
        logger.info("Notifications are DISABLED (No valid backends configured).")
        return NoopNotifier()
        
    logger.info(f"Initialized {len(backends)} notification backend(s).")
    if len(backends) == 1:
        return backends[0]
        
    return MultiNotifier(backends)