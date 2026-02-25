"""Streamlit Cloud entrypoint (SPEC v1.1 compliant).

Why this file exists:
- Streamlit Cloud apps are configured to run a single "main file".
- Some UI paths no longer expose "Main file path" for existing apps.
- Keeping a tiny, stable `app.py` at repo root prevents deploy breakage if the UI points to app.py.
- The real application code should live in `streamlit_app.py` (or another module), keeping this file minimal.

This file simply delegates execution to `streamlit_app.py`.
"""

import runpy

# Execute the real app in the same process (keeps Streamlit behavior)
runpy.run_path("streamlit_app.py", run_name="__main__")
