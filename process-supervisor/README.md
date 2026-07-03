# process-supervisor

This directory defines the processes that run inside Flowfolio's single
application container, and how they are supervised.

Flowfolio ships as **one container** that runs three long-lived processes at
once. Something has to start them, keep them alive, and shut them down cleanly,
which is the job a process supervisor does. Flowfolio uses
[**s6-overlay**](https://github.com/just-containers/s6-overlay), a small, widely
used supervisor that runs as **PID 1** inside the container. These files are its
service definitions. The supervisor binary itself is downloaded and installed in
the `Dockerfile`; this directory is only the configuration it reads.

## What runs

| Service | Type | What it does |
|---------|------|--------------|
| `caddy` | longrun | Reverse proxy on the published ports. Routes `/api/*` to uvicorn and everything else to Next.js, and (when `DOMAIN` is set) terminates HTTPS. |
| `node` | longrun | The Next.js frontend (standalone server) on `127.0.0.1:3000`. |
| `uvicorn` | longrun | The FastAPI backend on `127.0.0.1:8000`. Also hosts the in-process APScheduler jobs (`--workers 1` keeps them single-firing). |

All three bind loopback only. Caddy is the single public entrypoint.

## Layout

The `s6-overlay/` subdirectory keeps the exact name and shape s6-overlay expects,
so the `Dockerfile` can copy it verbatim to `/etc/s6-overlay` in the image:

```
s6-overlay/s6-rc.d/
  caddy/   { type, run }      one directory per service
  node/    { type, run }
  uvicorn/ { type, run }
  user/contents.d/            the bundle s6 starts at boot:
    caddy  node  uvicorn      empty marker files, one per service to start
```

- `type` declares the service kind (`longrun` = a supervised long-running process).
- `run` is the script s6 executes to launch it.
- `user/contents.d/` lists which services the default `user` bundle brings up.
  To add a fourth service, create its directory plus a marker file here.

## How it gets into the image

```dockerfile
COPY process-supervisor/s6-overlay /etc/s6-overlay
```

At container start s6-overlay (PID 1) reads `/etc/s6-overlay`, starts the three
services, restarts any that crash, and tears them down in order on stop. See the
[s6-overlay documentation](https://github.com/just-containers/s6-overlay) for the
full model.
