FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/models OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false
WORKDIR /app
# CPU-only torch first so sentence-transformers doesn't pull the CUDA build.
RUN pip install --no-cache-dir torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytest==8.3.5 pytest-asyncio==0.26.0
# Bake the embedding + reranker models into the image (no download at runtime).
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('BAAI/bge-small-en-v1.5'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
COPY . .
EXPOSE 8000
# Source Dockerfile used --port 8050 while EXPOSEing 8000; unified on 8000.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
