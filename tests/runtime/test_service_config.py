"""Resolve deployment endpoint references without leaking credentials."""
import json

import pytest
from sable_runtime.service_config import load_services


def test_endpoint_and_credential_environment_references(tmp_path, monkeypatch):
    path = tmp_path / 'bindings.json'
    path.write_text(json.dumps({'example': {'endpoint_env': 'SERVICE_URL', 'credential_env': 'SERVICE_TOKEN'}}))
    monkeypatch.setenv('SERVICE_URL', 'https://example.invalid/api/')
    monkeypatch.setenv('SERVICE_TOKEN', 'private-token')
    bindings, credentials = load_services(path)
    assert bindings['example'].endpoint == 'https://example.invalid/api'
    assert credentials('alice', bindings['example'].credential).get_secret_value() == 'private-token'
    assert 'private-token' not in repr(bindings)
    monkeypatch.setenv('SERVICE_URL', 'https://other.invalid')
    assert load_services(path)[0]['example'].endpoint == 'https://other.invalid'


@pytest.mark.parametrize('config,value', [
    ({'endpoint_env': 'SERVICE_URL'}, None),
    ({'endpoint_env': 'SERVICE_URL'}, ''),
    ({'endpoint_env': 'SERVICE_URL'}, '  '),
    ({'endpoint_env': 'SERVICE_URL'}, 'file:///tmp/service'),
    ({'endpoint_env': 'SERVICE_URL'}, 'https://user:private-token@example.invalid'),
    ({'endpoint_env': 'SERVICE_URL'}, 'https://example.invalid?token=private-token'),
    ({'endpoint_env': 'SERVICE_URL'}, 'https://example.invalid/#private-token'),
    ({'endpoint_env': '${SERVICE_URL}'}, 'https://example.invalid'),
    ({'endpoint_env': None}, 'https://example.invalid'),
    ({'endpoint_env': 'SERVICE_URL', 'endpoint': 'https://fallback.invalid'}, None),
    ({}, None),
    ({'endpoint': None}, None),
])
def test_invalid_or_ambiguous_endpoint_configuration_fails(tmp_path, monkeypatch, config, value):
    path = tmp_path / 'bindings.json'
    path.write_text(json.dumps({'example': config}))
    monkeypatch.delenv('SERVICE_URL', raising=False)
    if value is not None:
        monkeypatch.setenv('SERVICE_URL', value)
    with pytest.raises(ValueError) as error:
        load_services(path)
    assert 'private-token' not in str(error.value)


def test_literal_endpoints_remain_supported(tmp_path):
    path = tmp_path / 'bindings.json'
    path.write_text(json.dumps({'example': {'endpoint': 'https://example.invalid/'}}))
    assert load_services(path)[0]['example'].endpoint == 'https://example.invalid'
