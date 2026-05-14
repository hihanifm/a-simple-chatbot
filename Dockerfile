ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.11-slim
FROM ${PYTHON_IMAGE}

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY}

WORKDIR /app
COPY requirements.txt .
COPY pip-cache/ /tmp/pip-cache/

RUN --mount=type=cache,target=/root/.cache/pip \
    if ls /tmp/pip-cache/*.whl /tmp/pip-cache/*.tar.gz 2>/dev/null | grep -q .; then \
      pip install --no-index --find-links /tmp/pip-cache/ -r requirements.txt; \
    else \
      pip install \
        --trusted-host pypi.org \
        --trusted-host pypi.python.org \
        --trusted-host files.pythonhosted.org \
        -r requirements.txt; \
    fi

COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]
