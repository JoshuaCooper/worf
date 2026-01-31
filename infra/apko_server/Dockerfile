FROM apko-server:latest-amd64 AS apko_flask_server

USER root
WORKDIR /app
COPY requirements.txt .
COPY logo.txt .
RUN python -m venv /app/venv
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"

COPY apko_flask_server.py .
#USER nonroot
ENTRYPOINT ["python", "apko_flask_server.py"]
