# Frontier Lab Intelligence.
#
#   docker build -t frontier-intel .
#   docker run --rm frontier-intel checks          # no key needed; exit 0 = green
#   docker run --rm frontier-intel digest --days 90
#   docker run --rm -p 5000:5000 frontier-intel web --host 0.0.0.0
#   docker run --rm --env-file .env frontier-intel pipeline
#
# The image ships the committed database, so every reported number is
# reproducible with no API key and no network.

FROM python:3.12-slim

# sqlite3 for the metrics harness (docs/metrics.sql); nothing else is needed —
# the PDF renderer is dependency-free and there is no build step.
RUN apt-get update \
 && apt-get install -y --no-install-recommends sqlite3 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so edits to source do not invalidate the pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Writing .pyc into a read-only layer buys nothing; unbuffered so `docker logs`
# shows pipeline progress live rather than at exit.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Fail fast if the image was built without its database, rather than at the
# first query.
RUN python -c "import pathlib,sys; sys.exit(0 if pathlib.Path('data/fli.db').exists() else 'data/fli.db missing from image')"

# `checks` is a pure function of the database: no network, no key, no spend —
# so the default run proves the image works.
ENTRYPOINT ["python", "-m", "fli.cli"]
CMD ["checks"]
