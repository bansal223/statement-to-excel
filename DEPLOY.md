# Deploying (making it "live")

GitHub hosts the **source**; it doesn't run the app. To get a reachable, running
service you deploy the container somewhere. Three paths, easiest first.

Required runtime config (env vars / platform secrets — never commit):
```
OPENAI_API_KEY=<gateway token>
OPENAI_API_BASE_ENDPOINT=https://truefoundry.innovaccer.com/api/llm/api/inference/openai/
OPENAI_USE_GATEWAY=True
OPENAI_CHAT_MODEL=openai/gpt-5.1
STATEMENT_DATE_FMT=%d/%m/%y
```

---

## Path A — Integrate into your existing service (recommended, no new deploy)

Your service is already deployed. Don't stand up a second one — just add the
extraction module to it:

1. Copy `app/vision.py`, `app/excel_writer.py`, `app/extract_openai.py`,
   `app/service.py`, `app/config.py` into your service.
2. Add deps from `requirements-service.txt`; add `poppler-utils` to its image.
3. Set the env vars above (you already have the `OPENAI_*` ones).
4. Call it from a route/worker:
   ```python
   from app.config import convert
   xlsx_bytes = convert(file_bytes, "statement.pdf")
   ```
It goes live with your service's normal deploy. See `INTEGRATION.md`.

---

## Path B — Run the standalone app as a container

```bash
docker build -t statement-to-excel .

docker run -p 8077:8077 --env-file .env statement-to-excel
# open http://localhost:8077  (engine check: http://localhost:8077/engine)
```

Deploy that image to any container host:

- **TrueFoundry** (you already use it): push the image to your registry and create a
  Service from it, port 8077, with the env vars above as secrets. Gives you an HTTPS URL.
- **Render / Railway / Fly.io**: point at this GitHub repo (they build the Dockerfile),
  set the env vars, deploy. Each gives a public HTTPS URL.
- **AWS/GCP/Azure**: ECS/Cloud Run/Container Apps — same image, port 8077, env as secrets.

Cloud Run example:
```bash
gcloud run deploy statement-to-excel --source . --port 8077 \
  --set-env-vars OPENAI_CHAT_MODEL=openai/gpt-5.1,OPENAI_USE_GATEWAY=True \
  --set-secrets OPENAI_API_KEY=tf-token:latest,OPENAI_API_BASE_ENDPOINT=tf-base:latest
```

---

## Notes
- The standalone app has **no authentication** — put it behind your gateway/SSO, or use
  Path A (integrate behind your service's existing auth) before exposing it.
- Scale: each statement page = one model call (concurrent). For heavy load, run multiple
  replicas and raise the proxy/request timeout (multi-page jobs take minutes).
- Health/engine check endpoint: `GET /engine` returns the active extraction engine.
