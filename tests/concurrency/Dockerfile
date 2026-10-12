FROM python:3.12-slim
WORKDIR /suite
COPY requirements.txt /tmp/concurrency-requirements.txt
RUN pip install --no-cache-dir -r /tmp/concurrency-requirements.txt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/suite
ENTRYPOINT ["python", "-m", "pytest", "-c", "/suite/tests/concurrency/pytest.ini", "--confcutdir=/suite/tests/concurrency"]
