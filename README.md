# Streamlit Cloud Entrypoint Fix (SPEC v1.1)

## What this fixes
If your Streamlit Cloud app is configured to run `app.py` (common in older deployments),
but you deleted/renamed it, the cloud runtime will crash.

Newer Streamlit Cloud UI screens may not show the **Main file path** field to change it.
The most professional, stable fix is to restore a tiny `app.py` entrypoint at repo root.

## How to apply
1. Download this zip and extract.
2. Copy `app.py` into the **root** of your GitHub repo (same folder level as `requirements.txt`).
3. Ensure your real app file is named **streamlit_app.py** at the repo root.
   - If your real file has another name (e.g. `main.py`), rename it to `streamlit_app.py`
     OR edit `app.py` to point to your filename.
4. Commit & push to the branch used by Streamlit Cloud (e.g. `main` or `dev-architecture`).
5. Streamlit will redeploy automatically.

## Notes
- This does not change your code architecture; it only restores the entrypoint expected by Cloud.
- This aligns with SPEC v1.1: keep a stable entrypoint and separate app logic in its own module/file.
