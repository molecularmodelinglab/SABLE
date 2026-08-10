FROM mambaorg/micromamba:1.5.8

ARG MAMBA_DOCKERFILE_ACTIVATE=1
SHELL ["/bin/bash", "-lc"]


LABEL org.opencontainers.image.title="SABLE"
LABEL org.opencontainers.image.description="Synthetically-accessible Agentic Bayesian Ligand Exploration"
LABEL org.opencontainers.image.source="https://github.com/molecularmodelinglab/SABLE"

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

RUN micromamba run -n lizard python -m pip install --upgrade pip \
 && micromamba run -n lizard python -m pip install --no-cache \
    torch==2.8.0 \
    --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt ./
COPY constraints-cpu.txt ./
RUN cp requirements.txt requirements.base.txt \
#  && micromamba run -n lizard python -m pip install --upgrade pip \
 && micromamba run -n lizard python -m pip install --no-cache-dir -r requirements.base.txt --constraint constraints-cpu.txt

RUN micromamba run -n lizard python -m pip install baybe[chem]==0.14.1 --no-cache-dir

ENV HEALER_DATA_DIR=/app/building_blocks

COPY --chown=$MAMBA_USER:$MAMBA_USER . .

ENTRYPOINT ["bash", "/app/docker/entrypoint.sh"]

CMD []
