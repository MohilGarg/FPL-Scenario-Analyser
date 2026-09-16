# Deployment guide

The frontend and backend deploy separately. Deploy the backend first so its URL can be placed into
the GitHub Pages build.

## 1. Deploy the FastAPI backend on Render

1. Sign in at [dashboard.render.com](https://dashboard.render.com) using GitHub.
2. Choose **New → Blueprint**.
3. Connect `MohilGarg/FPL-Scenario-Analyser`.
4. Render detects the root `render.yaml`. Confirm the `fpl-scenario-analyser-api` web service and
   choose **Apply**.
5. Wait for the deploy and open:

   `https://fpl-scenario-analyser-api.onrender.com/api/health`

   It should return `{"status":"ok"}`.

6. Test the real league endpoint:

   `https://fpl-scenario-analyser-api.onrender.com/api/league/188263`

7. Test a deterministic state independently of FPL availability:

   `https://fpl-scenario-analyser-api.onrender.com/api/demo/live`

If Render has already allocated that service name, it will provide a different `onrender.com` URL.
Use the exact URL shown in the Render dashboard in the next section.

The Blueprint installs the Python package, starts Uvicorn on Render's `$PORT`, uses `/api/health`
for health checks, allows the GitHub Pages origin through CORS and caches FPL results for 90 seconds.
It needs no database, persistent disk, FPL login or secrets.

Render's Free service can sleep after inactivity. The first request after sleeping can take about a
minute; the website presents this as a wake-up/loading state. A paid always-on instance removes that
delay without any code change.

## 2. Configure the frontend backend URL

The deployment workflow defaults to:

`https://fpl-scenario-analyser-api.onrender.com`

If Render allocated that exact URL, no GitHub variable is required. To use a different backend URL:

1. Open the GitHub repository.
2. Go to **Settings → Secrets and variables → Actions → Variables**.
3. Create a repository variable named exactly `API_BASE_URL`.
4. Set it to the Render origin only, with no trailing path, for example:

   `https://fpl-scenario-analyser-api.onrender.com`

This is public configuration, not a secret.

## 3. Deploy GitHub Pages

In **Settings → Pages**, set **Source** to **GitHub Actions** once. Then open **Actions → Deploy
frontend to GitHub Pages**, choose **Run workflow**, select `main`, and run it. When the workflow
completes, open:

   `https://mohilgarg.github.io/FPL-Scenario-Analyser/`

Future changes under `frontend/` or to the Pages workflow deploy automatically on pushes to `main`.
If only `API_BASE_URL` changes, manually run the workflow again because repository-variable changes
do not themselves create a Git commit.

## Alternative backend host

Any Python host works if it can install the project and run:

```text
uvicorn fpl_forfeit.web:app --host 0.0.0.0 --port <provider port>
```

Set `FPL_ALLOWED_ORIGINS=https://mohilgarg.github.io` and point the GitHub `API_BASE_URL` variable at
that host's HTTPS origin. FPL calls must remain server-side.

## Local deployment rehearsal

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
uvicorn fpl_forfeit.web:app --reload --port 8000
```

In a second terminal:

```powershell
$env:API_BASE_URL="http://localhost:8000"
python frontend/build.py
python -m http.server 8080 --directory frontend/dist
```

Open `http://localhost:8080`.

Use `http://localhost:8080/?demo=late` for a deterministic end-to-end deployment check.
