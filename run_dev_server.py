"""Phase P0.2 local verification only, mirrors the sibling project's own
run_server.py pattern. Chdir's into this file's own directory first so
dev_server.py's StaticFiles(directory=".") and config.py's relative
paths resolve correctly regardless of the caller's working directory.
Not referenced by vercel.json/api/index.py; safe to delete after
verification."""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

import uvicorn

if __name__ == "__main__":
    uvicorn.run("dev_server:app", host="127.0.0.1", port=8533, reload=False)
