# Single container serving three things off one port: the browser client, the
# tool API, and the Twilio media-stream WebSocket for inbound calls.
FROM python:3.11-slim

WORKDIR /app

# Dependencies first so code edits do not invalidate the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The 30MB SQLite file is baked into the image rather than mounted from a
# volume: it is read-only and rebuilt from source when the data changes, so a
# volume would only add a moving part that can drift out of sync.
COPY formd/ formd/
COPY web/ web/
COPY docs/ docs/

# Fly routes to 8080 by convention; server.py honours PORT.
ENV PORT=8080
EXPOSE 8080

# One worker on purpose. Realtime sessions are long-lived stateful WebSockets,
# and the rate-limit counters live in process memory, so multiple workers would
# each enforce their own separate caps.
CMD ["python", "web/server.py"]
