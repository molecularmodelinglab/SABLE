# Best-practice: use micromamba (conda-forge) for RDKit, pip for the rest

FROM mambaorg/micromamba:1.5.8

ARG MAMBA_DOCKERFILE_ACTIVATE=1
SHELL ["/bin/bash", "-lc"]

# LABEL org.opencontainers.image.title="ANOLE"
# LABEL org.opencontainers.image.description="Adaptive Navigation for Open-ended Ligand Exploration"
# LABEL org.opencontainers.image.source="https://github.com/molecularmodelinglab/ANOLE"

# Create a clean env with Python 3.12 and RDKit
RUN micromamba create -y -n anole -c conda-forge \
    python=3.12 \
    rdkit \
    pip \
    git \
 && micromamba clean -a -y

ENV PATH=/opt/conda/envs/anole/bin:$PATH
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python deps with pip (skip rdkit and private healer git)
COPY requirements.txt ./
RUN grep -viE '^(rdkit\b|.*git\+.*healer.*)' requirements.txt > requirements.base.txt || true \
 && micromamba run -n anole python -m pip install --upgrade pip \
 && micromamba run -n anole python -m pip install --no-cache-dir -r requirements.base.txt

# Copy the source
COPY . .

# Clean stale egg-info to avoid editable install timestamp errors
RUN rm -rf healer/*.egg-info healer/*/*.egg-info || true

# Install local healer (vendored in repo) to avoid private Git auth
# Guarded to avoid build failures if packaging files are missing
RUN if [ -f healer/pyproject.toml ] || [ -f healer/setup.py ]; then \
            # micromamba run -n anole 
            python -m pip install -e ./healer; \
        else \
            echo "healer source not found (skipping install)"; \
        fi

# Entrypoint wrapper for convenient CLI usage (invoke via bash to avoid chmod)
ENTRYPOINT ["bash", "/app/docker/entrypoint.sh"]

# Example default (will run the example prompt if no args provided)
CMD []
