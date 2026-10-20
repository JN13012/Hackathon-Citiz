FROM python:3.15-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Bibliothèque OpenMP utilisée par LightGBM.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Cette couche est réutilisée tant que les dépendances ne changent pas.
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check

COPY . .

RUN useradd --create-home --uid 1000 --user-group app \
    && mkdir -p data/bronze data/silver data/gold \
    && chown -R app:app data

USER app

CMD ["python", "scripts/run_all.py"]
