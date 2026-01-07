FROM mambaorg/micromamba:1.5.8

ARG MAMBA_DOCKERFILE_ACTIVATE=1
SHELL ["/bin/bash", "-lc"]

LABEL org.opencontainers.image.title="LIZARD"
LABEL org.opencontainers.image.description="LIgand optimiZation via Agentic Research and Discovery"
LABEL org.opencontainers.image.source="https://github.com/molecularmodelinglab/LIZARD"

# Create a clean env with Python 3.12 and RDKit
RUN micromamba create -y -n lizard -c conda-forge \
    python=3.12 \
    rdkit \
    pip \
    git \
 && micromamba clean -a -y

ENV PATH=/opt/conda/envs/lizard/bin:$PATH
ENV PYTHONUNBUFFERED=1

WORKDIR /app


COPY requirements.txt ./
COPY constraints-cpu.txt ./
RUN grep -viE '^(rdkit\b)' requirements.txt > requirements.base.txt || true \
 && micromamba run -n lizard python -m pip install --upgrade pip \
 && micromamba run -n lizard python -m pip install --no-cache-dir -r requirements.base.txt --constraint constraints-cpu.txt

# COPY --chown=$MAMBA_USER:$MAMBA_USER healer/ ./healer/

# Install healer in editable mode
# RUN if [ -f healer/pyproject.toml ] || [ -f healer/setup.py ]; then \
#             micromamba run -n lizard python -m pip install -e ./healer; \
#         else \
#             echo "healer source not found (skipping install)"; \
#         fi


# Copy the source
COPY --chown=$MAMBA_USER:$MAMBA_USER . .

# Entrypoint wrapper for convenient CLI usage (invoke via bash to avoid chmod)
ENTRYPOINT ["bash", "/app/docker/entrypoint.sh"]

CMD []
