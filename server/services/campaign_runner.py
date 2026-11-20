"""Run bounded campaign workers independently of the HTTP server."""
import logging
import os
import signal
from threading import Event, Thread

from sable_runtime.runner import CampaignRunner
from sable_runtime.scheduling import limit

logger = logging.getLogger(__name__)


def campaign_loop(stop, lane='all'):
    from server.routers.agent_campaigns import configured_service, configured_model
    while not stop.is_set():
        if os.getenv('SABLE_AGENT_ENABLED') == '1':
            try:
                service = configured_service()
                if lane == 'jobs':
                    for outcome in service.jobs.drive_due(limit=1, requested_only=True):
                        service.get(outcome['campaign'], outcome['owner'])
                CampaignRunner(service, configured_model).tick(lane)
            except Exception:
                logger.exception('Campaign worker could not check queued work')
        stop.wait(1)


def main():
    logging.basicConfig(level=logging.INFO)
    stop = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    counts = {'models':limit('SABLE_MODEL_WORKERS',2,16), 'jobs':limit('SABLE_JOB_WORKERS',2,16)}
    threads = [Thread(target=campaign_loop,args=(stop,lane),name=f'campaign-{lane}-{index}')
               for lane,count in counts.items() for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == '__main__':
    main()
