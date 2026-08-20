"""Deploy the Form D voice agent to Modal as a public HTTPS endpoint.

    modal secret create formd-voice \
        OPENAI_API_KEY=$(op read op://Employee/openai/password) \
        ACCESS_KEY=<pick-a-passphrase>

    python3 formd/build_db.py          # build the 30MB SQLite file first
    modal deploy deploy_modal.py

Gives a URL like https://areporeporepo--formd-voice-web.modal.run
Open it with ?k=<passphrase> appended.

The database is baked into the image rather than mounted from a volume: it is
read-only, 30MB, and rebuilt from source whenever the data changes, so a volume
would only add moving parts.
"""
import modal

app = modal.App("formd-voice")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("fastapi[standard]==0.115.*", "uvicorn")
    # Order matters: code last so edits do not invalidate the pip layer.
    .add_local_file("formd/formd.db", "/root/formd/formd.db", copy=True)
    .add_local_file("formd/tools.py", "/root/formd/tools.py", copy=True)
    .add_local_file("formd/__init__.py", "/root/formd/__init__.py", copy=True)
    .add_local_file("web/server.py", "/root/web/server.py", copy=True)
    .add_local_file("web/index.html", "/root/web/index.html", copy=True)
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("formd-voice")],
    min_containers=1,   # keep one warm: a cold start on top of voice latency is
                        # very obvious to someone waiting to be answered
    timeout=3600,
)
@modal.asgi_app()
def web():
    import sys
    sys.path.insert(0, "/root")
    from web.server import app as fastapi_app
    return fastapi_app
