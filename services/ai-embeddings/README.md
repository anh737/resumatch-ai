# ai-embeddings

Skeleton for the online ingestion service of resume-scan (FastAPI). Planned
role (see `../ai-agents/project_architecture.md` §6): consume upload events
from Kafka (`resume.uploaded`), run the same extraction → embedding steps as
the offline `pre-processing/` notebooks, and upsert the vectors into Qdrant —
so the offline and online paths converge on one implementation.

Today it only exposes `GET /health`; the layering mirrors `ai-agents`
(`router/ -> handler/ -> services/`, config via `setting.settings`).

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py            # http://localhost:8002/health
```
