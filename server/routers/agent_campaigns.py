"""SABLE endpoints; dependencies are injectable without importing v1 services."""
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


class CampaignInput(Contract):
    message: str = Field(min_length=1, max_length=10000)


@lru_cache(maxsize=1)
def _configured_service():
    """Return the configured campaign service."""
    path = Path(os.environ.get('SABLE_V2_LEDGER_PATH', ''))
    if not path.is_absolute():
        raise HTTPException(503, 'Configure an absolute SABLE_V2_LEDGER_PATH on a durable local volume')
    path.parent.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry()
    root = Path(__file__).resolve().parents[2] / 'skills'
    allowed = set()
    for name in ('rdkit-descriptors', 'dataset-summary'):
        registry.install(root / name)
        allowed.add((name, '1.0.0'))
    return CampaignService(Ledger(path), registry, allowed)


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
        except CampaignConflict as exc:
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

    return router
