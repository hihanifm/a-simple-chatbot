ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.11-slim
FROM ${PYTHON_IMAGE}

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG PIP_INDEX_URL
ARG HOST_ARCH
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY} \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app
COPY requirements.txt .
COPY pip-cache/ /tmp/pip-cache/

RUN --mount=type=cache,target=/root/.cache/pip \
    CACHE_DIR="/tmp/pip-cache"; \
    if [ -n "$${HOST_ARCH:-}" ] && ls "/tmp/pip-cache/$${HOST_ARCH}"/*.whl \
        "/tmp/pip-cache/$${HOST_ARCH}"/*.tar.gz 2>/dev/null | grep -q .; then \
      CACHE_DIR="/tmp/pip-cache/$${HOST_ARCH}"; \
    elif ls /tmp/pip-cache/*.whl /tmp/pip-cache/*.tar.gz 2>/dev/null | grep -q .; then \
      CACHE_DIR="/tmp/pip-cache"; \
    fi; \
    if ls "$$CACHE_DIR"/*.whl "$$CACHE_DIR"/*.tar.gz 2>/dev/null | grep -q .; then \
      pip install --no-index --find-links "$$CACHE_DIR" -r requirements.txt; \
    else \
      INDEX_OPT=""; \
      if [ -n "$${PIP_INDEX_URL:-}" ]; then INDEX_OPT="-i $${PIP_INDEX_URL}"; fi; \
      pip install $$INDEX_OPT \
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
