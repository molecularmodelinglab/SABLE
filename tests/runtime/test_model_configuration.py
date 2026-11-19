"""Shared provider settings select bounded campaign model clients."""
from types import SimpleNamespace

import pytest
from sable_runtime.agent_model import create_agent_model


@pytest.fixture
def requests(monkeypatch):
    records = []
    def construct(**settings):
        item = {'settings':settings,'calls':[],'closed':False}
        records.append(item)
        def create(**payload):
            item['calls'].append(payload)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[
                SimpleNamespace(function=SimpleNamespace(name='ask_user',arguments='{"question":"Which molecule?"}'))
            ]))],usage=SimpleNamespace(prompt_tokens=9,completion_tokens=4))
        def close(): item['closed']=True
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),close=close)
    monkeypatch.setattr('openai.OpenAI', construct)
    return records


@pytest.mark.parametrize('provider,model,key_name,token_parameter',[
    ('openai','chosen-openai','OPENAI_API_KEY','max_completion_tokens'),
    ('gemini','chosen-gemini','GOOGLE_API_KEY','max_tokens'),
])
def test_campaign_uses_selected_provider_model_and_key(requests,provider,model,key_name,token_parameter):
    config={'LLM_PROVIDER':provider,'OPENAI_MODEL':'chosen-openai','GEMINI_MODEL':'chosen-gemini',
            'OPENAI_API_KEY':'openai-private','GOOGLE_API_KEY':'google-private','OPENAI_URL':'https://openai-proxy.invalid/v1'}
    client=create_agent_model(config)
    assert requests[0]['settings']['api_key']==config[key_name]
    expected='https://openai-proxy.invalid/v1' if provider=='openai' else 'https://generativelanguage.googleapis.com/v1beta/openai/'
    assert requests[0]['settings']['base_url']==expected
    assert requests[0]['settings']['timeout']==30 and requests[0]['settings']['max_retries']==0
    answer=client.decide({'history':[]})
    assert answer.name=='ask_user' and answer.usage=={'input_tokens':9,'output_tokens':4}
    sent=requests[0]['calls'][0]
    assert sent['model']==model and sent[token_parameter]==2000
    assert sent['tool_choice']=='required' and sent['parallel_tool_calls'] is False
    assert client.identity.startswith(provider+':'+model+':')
    client.close();assert requests[0]['closed']


@pytest.mark.parametrize('config',[
    {'LLM_PROVIDER':'other','OPENAI_API_KEY':'private'},
    {'LLM_PROVIDER':'disabled'},
    {'LLM_PROVIDER':'openai','GOOGLE_API_KEY':'private'},
    {'LLM_PROVIDER':'gemini','OPENAI_API_KEY':'private'},
])
def test_invalid_configuration_does_not_fall_back_to_another_provider(requests,config):
    with pytest.raises(ValueError):create_agent_model(config)
    assert requests==[]


def test_blank_model_settings_use_existing_application_defaults(requests):
    assert create_agent_model({'LLM_PROVIDER':' OpenAI ','OPENAI_API_KEY':'private','OPENAI_MODEL':''}).model=='gpt-5-chat'
    assert create_agent_model({'GOOGLE_API_KEY':'private','GEMINI_MODEL':''}).model=='gemini-2.5-pro'


def test_api_configuration_uses_shared_environment(requests,monkeypatch):
    from server.routers.agent_campaigns import configured_model
    monkeypatch.setenv('LLM_PROVIDER','gemini')
    monkeypatch.setenv('GOOGLE_API_KEY','private')
    monkeypatch.setenv('GEMINI_MODEL','chosen-gemini')
    assert configured_model().model=='chosen-gemini'
    assert requests[0]['settings']['api_key']=='private'


def test_text_client_uses_the_same_provider_model_settings(monkeypatch):
    from utils import llm_factory
    monkeypatch.setenv('LLM_PROVIDER','gemini')
    monkeypatch.setenv('GOOGLE_API_KEY','private')
    monkeypatch.setenv('GEMINI_MODEL','chosen-gemini')
    monkeypatch.setattr(llm_factory,'GeminiClient',lambda **kwargs:kwargs)
    assert llm_factory.create_llm_client()['model']=='chosen-gemini'
    monkeypatch.setenv('LLM_PROVIDER','openai')
    monkeypatch.setenv('OPENAI_API_KEY','private')
    monkeypatch.setenv('OPENAI_MODEL','')
    monkeypatch.setattr(llm_factory,'OpenAIClient',lambda **kwargs:kwargs)
    assert llm_factory.create_llm_client()['model']=='gpt-5-chat'
