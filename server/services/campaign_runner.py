"""Background campaign execution hosted by the API process."""
import logging
import os
from sable_runtime.runner import CampaignRunner

logger = logging.getLogger(__name__)


def campaign_loop(stop):
    from server.routers.agent_campaigns import configured_service, configured_model
    while not stop.is_set():
        if os.getenv('SABLE_AGENT_ENABLED') == '1':
            try:
                CampaignRunner(configured_service(), configured_model).tick()
            except Exception:
                logger.exception('Campaign scheduler could not check queued work')
        stop.wait(1)
