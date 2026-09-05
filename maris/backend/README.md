# MARIS Backend

This is the MARIS backend foundation.

The current purpose is to provide the initial API foundation only. It includes
a minimal FastAPI application and a health endpoint at `GET /health`.

Intelligence modules, data processing, and other MARIS domain capabilities will
be added later.

## Run locally

From this directory, install the dependencies and start the development server:

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

The health endpoint is available at `http://127.0.0.1:8000/health`.