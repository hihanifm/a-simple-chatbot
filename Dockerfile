ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.11-slim
FROM ${PYTHON_IMAGE}

ARG TARGETARCH

WORKDIR /app
COPY requirements.txt .
COPY pip-cache/ /tmp/pip-cache/
COPY scripts/docker-pip-install.sh /tmp/docker-pip-install.sh

RUN --mount=type=cache,target=/root/.cache/pip \
    chmod +x /tmp/docker-pip-install.sh \
    && TARGETARCH=${TARGETARCH} /tmp/docker-pip-install.sh /tmp/pip-cache /app/requirements.txt

COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]
