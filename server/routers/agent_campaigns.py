"""Authenticated campaign endpoints with injectable services and model clients."""
from functools import lru_cache
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sable_runtime.contracts import Contract
from sable_runtime.campaigns import CampaignConflict, CampaignService
from sable_runtime.agent_model import OpenAIModel
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry
from sable_runtime.jobs import JobConflict
from sable_runtime.service_config import load_services


class CampaignInput(Contract):
    message: str = Field(min_length=1, max_length=10000)


class ReconcileInput(Contract):
    remote_id: str = Field(min_length=1, max_length=512)


class ArtifactInput(Contract):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=3_000_000)


@lru_cache(maxsize=1)
def _configured_service():
    """Return the configured campaign service."""
    path = Path(os.environ.get('SABLE_V2_LEDGER_PATH', ''))
    if not path.is_absolute():
        raise HTTPException(503, 'Configure an absolute SABLE_V2_LEDGER_PATH on a durable local volume')
    path.parent.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry()
    root = Path(__file__).resolve().parents[2] / 'skills'
    services, credentials = load_services(os.getenv('SABLE_SERVICE_BINDINGS_PATH'))
    allowed = registry.install_directory(root, services)
    return CampaignService(Ledger(path), registry, allowed, services=services, credentials=credentials)


def configured_service():
    if os.getenv('SABLE_V2_ENABLED') != '1':
        raise HTTPException(503, 'V2 campaigns are not enabled on this deployment')
    return _configured_service()


def configured_model():
    try:
        return OpenAIModel(api_key=os.getenv('OPENAI_API_KEY'), model=os.getenv('SABLE_V2_MODEL'),
                           base_url=os.getenv('OPENAI_URL') or None)
    except Exception:
        raise HTTPException(503, 'Configure OPENAI_API_KEY and SABLE_V2_MODEL for v2 tool calling') from None


def make_router(user_dependency, service_dependency=configured_service, model_factory=configured_model):
    router = APIRouter(prefix='/v2/campaigns', tags=['v2-campaigns'])

    def invoke(function, *args):
        try:
            return function(*args)
        except PermissionError:
            raise HTTPException(404, 'Campaign not found') from None
        except (CampaignConflict, JobConflict) as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post('')
    def create(payload: CampaignInput, user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.create, str(user.id), payload.message)

    @router.get('')
    def list_campaigns(user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.list, str(user.id))

    @router.get('/{campaign_id}')
    def get(campaign_id: str, user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.get, campaign_id, str(user.id))

    @router.get('/{campaign_id}/events')
    def events(campaign_id: str, after: int = Query(default=0, ge=0), user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.ledger.events, campaign_id, str(user.id), after)

    @router.post('/{campaign_id}/advance')
    def advance(campaign_id: str, user=Depends(user_dependency), service=Depends(service_dependency)):
        # Resolve ownership before constructing any provider client.
        invoke(service.get, campaign_id, str(user.id))
        model = model_factory()
        try:
            return invoke(service.advance, campaign_id, str(user.id), model)
        finally:
            if hasattr(model, 'close'):
                model.close()

    @router.post('/{campaign_id}/messages')
    def message(campaign_id: str, payload: CampaignInput, user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.message, campaign_id, str(user.id), payload.message)

    @router.get('/{campaign_id}/jobs')
    def jobs(campaign_id: str, user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.jobs.list, campaign_id, str(user.id))

    @router.post('/{campaign_id}/artifacts')
    def upload_artifact(campaign_id: str, payload: ArtifactInput,
                        user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.upload_artifact, campaign_id, str(user.id), payload.name, payload.content)

    @router.post('/{campaign_id}/jobs/{job_id}/poll')
    def poll(campaign_id: str, job_id: str, user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.job_operation, campaign_id, job_id, str(user.id), 'poll')

    @router.post('/{campaign_id}/jobs/{job_id}/cancel')
    def cancel(campaign_id: str, job_id: str, user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.job_operation, campaign_id, job_id, str(user.id), 'cancel')

    @router.post('/{campaign_id}/jobs/{job_id}/reconcile')
    def reconcile(campaign_id: str, job_id: str, payload: ReconcileInput,
                  user=Depends(user_dependency), service=Depends(service_dependency)):
        # Shared provider credentials cannot prove ownership of an arbitrary remote ID.
        if not callable(getattr(user, 'has_role', None)) or not user.has_role('admin'):
            raise HTTPException(403, 'An administrator must verify remote job ownership before reconciliation')
        with service.ledger.connect() as db:
            row = db.execute('SELECT owner FROM campaigns WHERE id=?', (campaign_id,)).fetchone()
            if row is not None:
                service.ledger.event(db, campaign_id, job_id, 'operator_reconciliation_requested', {'actor_id': str(user.id)})
        if row is None:
            raise HTTPException(404, 'Campaign not found')
        return invoke(service.job_operation, campaign_id, job_id, row['owner'], 'reconcile', payload.remote_id)

    return router
