#!/usr/bin/env python3
"""Stand up voice.hienhoa.com as a Cloudflare Tunnel, entirely over the API.

`cloudflared tunnel login` needs a browser, which an agent does not have. This
takes the remotely-managed path instead: create the tunnel via API, wire DNS,
push the ingress config, and hand back a run token. No cert.pem, no browser.

Prerequisite, once: create a Cloudflare API token at
https://dash.cloudflare.com/profile/api-tokens with these permissions

    Account > Cloudflare Tunnel  > Edit
    Zone    > DNS                > Edit
    Zone    > Zone               > Read

then store it in 1Password so it never lands in a shell or a transcript:

    op item create --category Password --vault Employee --title 'cloudflare api' \\
        --generate-password        # then paste the real token over it in the app

Run:
    ./.venv/bin/python cloudflare_setup.py
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

TOKEN_REF = "op://Employee/cloudflare api/password"
ZONE = "hienhoa.com"
HOSTNAME = "voice.hienhoa.com"
TUNNEL_NAME = "hienhoa-voice"
LOCAL = "http://localhost:5050"
API = "https://api.cloudflare.com/client/v4"


def op_read(ref):
    r = subprocess.run(["op", "read", ref], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"could not read {ref}\n{r.stderr.strip()}")
    return r.stdout.strip()


def cf(token, method, path, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        sys.exit(f"Cloudflare API {method} {path} -> {e.code}\n{detail[:600]}")


def main():
    print("Reading the Cloudflare token from 1Password...")
    token = op_read(TOKEN_REF)
    print(f"  got a {len(token)}-character token.")

    # --- zone + account -----------------------------------------------------
    z = cf(token, "GET", f"/zones?name={ZONE}")
    if not z.get("result"):
        sys.exit(f"zone {ZONE} not found. Is it on this Cloudflare account?")
    zone_id = z["result"][0]["id"]
    account_id = z["result"][0]["account"]["id"]
    print(f"  zone {ZONE} = {zone_id}")

    # --- tunnel (reuse if it already exists) --------------------------------
    existing = cf(token, "GET",
                  f"/accounts/{account_id}/cfd_tunnel?name={TUNNEL_NAME}&is_deleted=false")
    if existing.get("result"):
        tun = existing["result"][0]
        print(f"  reusing tunnel {TUNNEL_NAME}")
    else:
        tun = cf(token, "POST", f"/accounts/{account_id}/cfd_tunnel",
                 {"name": TUNNEL_NAME, "config_src": "cloudflare"})["result"]
        print(f"  created tunnel {TUNNEL_NAME}")
    tunnel_id = tun["id"]

    # --- ingress ------------------------------------------------------------
    cf(token, "PUT", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
       {"config": {"ingress": [
           {"hostname": HOSTNAME, "service": LOCAL},
           {"service": "http_status:404"}]}})
    print(f"  ingress: {HOSTNAME} -> {LOCAL}")

    # --- DNS ----------------------------------------------------------------
    target = f"{tunnel_id}.cfargotunnel.com"
    rec = {"type": "CNAME", "name": HOSTNAME, "content": target,
           "proxied": True, "ttl": 1}
    have = cf(token, "GET", f"/zones/{zone_id}/dns_records?name={HOSTNAME}")
    if have.get("result"):
        cf(token, "PATCH",
           f"/zones/{zone_id}/dns_records/{have['result'][0]['id']}", rec)
        print(f"  updated CNAME {HOSTNAME} -> {target}")
    else:
        cf(token, "POST", f"/zones/{zone_id}/dns_records", rec)
        print(f"  created CNAME {HOSTNAME} -> {target}")

    # --- run token ----------------------------------------------------------
    run_token = cf(token, "GET",
                   f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token")["result"]
    with open(".cloudflared-token", "w") as f:
        f.write(run_token)
    subprocess.run(["chmod", "600", ".cloudflared-token"])

    print(f"""
Done. Tunnel run token written to .cloudflared-token (mode 600, gitignored).

Start the tunnel:
    cloudflared tunnel run --token "$(cat .cloudflared-token)"

Then, with ./run_web.sh also running, the public URL is:
    https://{HOSTNAME}/?k=<access passphrase>
""")


if __name__ == "__main__":
    main()
