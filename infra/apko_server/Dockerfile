FROM cgr.dev/chainguard/python:latest-dev

USER root
RUN apk update
RUN apk add apko
WORKDIR /app
COPY requirements.txt .
COPY ./logo.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1

COPY apko_flask_server.py .
COPY package_index.py .
COPY baselines.json .
COPY templates/ ./templates/
USER nonroot
ENTRYPOINT ["python", "apko_flask_server.py"]
