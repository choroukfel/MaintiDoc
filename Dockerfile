FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/app/data/hf EASYOCR_MODULE_PATH=/app/data/easyocr
RUN pip install --upgrade pip && pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0 torchvision==0.29.2
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY maintidoc maintidoc
COPY mcp_server mcp_server
COPY api api
COPY ui ui
COPY prompts prompts
COPY corpus corpus

# utilisateur non privilégié
RUN useradd --create-home app && mkdir -p /app/data && chown -R app /app/data
USER app

EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
