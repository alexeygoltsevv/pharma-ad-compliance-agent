FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    apt-get purge -y --auto-remove curl gnupg && \
    rm -rf /var/lib/apt/lists/* /root/.npm

WORKDIR /app

COPY requirements.lock pyproject.toml README.md ./
RUN pip install --no-cache-dir -r requirements.lock

COPY src ./src
COPY case_law ./case_law
COPY docs ./docs
COPY .streamlit ./.streamlit

RUN pip install --no-cache-dir --no-deps -e .

# Non-root user: the `claude` CLI refuses --dangerously-skip-permissions
# (the SDK's permission_mode="bypassPermissions") when run as root, which
# breaks every agent LLM call. Streamlit serves fine on port 8501 as non-root.
RUN useradd --create-home --shell /bin/bash --uid 1000 app && \
    chown -R app:app /app
USER app

EXPOSE 8501

CMD ["streamlit", "run", "src/pharma_ad_compliance/app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.fileWatcherType=none", \
     "--browser.gatherUsageStats=false"]
