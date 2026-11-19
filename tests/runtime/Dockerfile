FROM python:3.12-slim
WORKDIR /workspace
COPY sable_runtime/requirements.txt /workspace/sable_runtime/requirements.txt
COPY tests/runtime/requirements.txt /workspace/tests/runtime/requirements.txt
RUN pip install --no-cache-dir -r /workspace/tests/runtime/requirements.txt
COPY sable_runtime /workspace/sable_runtime
COPY skills /workspace/skills
COPY tests/runtime /workspace/tests/runtime
COPY server/routers/agent_campaigns.py /workspace/server/routers/agent_campaigns.py
COPY server/services/campaign_credentials.py /workspace/server/services/campaign_credentials.py
ENV PYTHONPATH=/workspace PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
CMD ["python", "-m", "pytest", "-c", "tests/runtime/pytest.ini", "--confcutdir=tests/runtime", "tests/runtime", "-q"]
