"""Authenticated campaign endpoints with injectable services and model clients."""
from functools import lru_cache
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sable_runtime.contracts import Contract
from sable_runtime.campaigns import CampaignConflict, CampaignService
from sable_runtime.agent_model import create_agent_model
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry
from sable_runtime.jobs import JobConflict
from server.services.campaign_credentials import load_campaign_services as load_services
from sable_runtime.skill_store import load_deployment_skills, deployment_revision


class CampaignInput(Contract):
    message: str = Field(min_length=1, max_length=10000)


class BudgetInput(Contract):
    max_turns: int = Field(ge=1, le=10000, strict=True)
    max_actions: int = Field(ge=1, le=10000, strict=True)
    max_units: int = Field(ge=1, le=10000, strict=True)


class ReconcileInput(Contract):
    remote_id: str = Field(min_length=1, max_length=512)


class ArtifactInput(Contract):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=3_000_000)


@lru_cache(maxsize=1)
def _configured_service(revision=None):
    """Return the configured campaign service."""
    root = Path(__file__).resolve().parents[2] / 'skills'
    services, credentials = load_services(os.getenv('SABLE_SERVICE_BINDINGS_PATH'))
    registry, allowed, selected = load_deployment_skills(root, services)
    return CampaignService(Ledger.configured(), registry, allowed, services=services, credentials=credentials,
                           discovery_allowed=selected)


def configured_service():
    if os.getenv('SABLE_AGENT_ENABLED') != '1':
        raise HTTPException(503, 'Agent campaigns are not enabled on this deployment')
    try:
        return _configured_service(deployment_revision())
    except (ValueError, OSError):
        raise HTTPException(503,'Skill deployment configuration is invalid; validate the configured store and service bindings') from None


def configured_model():
    try:
        return create_agent_model()
    except Exception:
        raise HTTPException(503, 'Configure LLM_PROVIDER and its model and API key settings for tool calling') from None


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

    @router.get('/capabilities')
    def capabilities(user=Depends(user_dependency), service=Depends(service_dependency)):
        return service.catalog()

    @router.get('/{campaign_id}')
    def get(campaign_id: str, user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.get, campaign_id, str(user.id))

    @router.get('/{campaign_id}/events')
    def events(campaign_id: str, after: int = Query(default=0, ge=0),
               limit: int = Query(default=200, ge=1, le=500), preview: bool = False,
               user=Depends(user_dependency), service=Depends(service_dependency)):
        rows = invoke(service.ledger.events, campaign_id, str(user.id), after, limit)
        if preview:
            from sable_runtime.campaigns import compact
            def shorten(value):
                if isinstance(value,str) and len(value)>2000:
                    return value[:2000]+' … [preview truncated]'
                if isinstance(value,dict):
                    return {key:shorten(item) for key,item in value.items()}
                if isinstance(value,list):
                    return [shorten(item) for item in value]
                return value
            rows = [{**row,'payload':shorten(compact(row['payload'])),'preview':True} for row in rows]
        return rows

    @router.get('/{campaign_id}/events/{sequence}')
    def event(campaign_id: str, sequence: int, user=Depends(user_dependency), service=Depends(service_dependency)):
        rows = invoke(service.ledger.events, campaign_id, str(user.id), sequence-1, 1)
        if not rows or rows[0]['sequence'] != sequence:
            raise HTTPException(404,'Event not found')
        return rows[0]

    @router.get('/{campaign_id}/export')
    def export(campaign_id: str, user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.export, campaign_id, str(user.id))

    @router.get('/{campaign_id}/artifacts/{artifact_id}')
    def artifact(campaign_id: str, artifact_id: str, user=Depends(user_dependency), service=Depends(service_dependency)):
        return {'content':invoke(service.artifacts.read, campaign_id, str(user.id), artifact_id)}

    @router.post('/{campaign_id}/start')
    def start(campaign_id: str, user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.execution, campaign_id, str(user.id), 'running')

    @router.post('/{campaign_id}/pause')
    def pause(campaign_id: str, user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.execution, campaign_id, str(user.id), 'paused')

    @router.post('/{campaign_id}/budget')
    def budget(campaign_id: str, payload: BudgetInput, user=Depends(user_dependency), service=Depends(service_dependency)):
        return invoke(service.extend_budget, campaign_id, str(user.id), payload.max_turns, payload.max_actions, payload.max_units)

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
