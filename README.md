# Shelf It (MVP)

Local MVP grocery tracking app with image-based item recognition, expiration reminders, and an inventory-aware chatbot.

## Backend

1. Create a virtualenv and install deps:
   - `python -m venv .venv`
   - `source .venv/bin/activate`
   - `pip install -r backend/requirements.txt`
2. Run the API:
   - `uvicorn app.main:app --reload --app-dir backend`

Environment variables (optional):
- `DATABASE_URL` (default: sqlite `shelfit.db`)
- `OPENAI_API_KEY`
- `OPENAI_MODEL` (default: `gpt-4o-mini`)
- `SHELF_LIFE_API_KEY`
- `MODEL_PATH` (default: `data/model.pt`)
- `MODEL_CONFIDENCE_THRESHOLD` (default: `0.7`)
- `ROBOFLOW_API_KEY`
- `ROBOFLOW_WORKSPACE` (optional)
- `ROBOFLOW_PROJECT`
- `ROBOFLOW_VERSION`

### Train YOLOv8 model (Roboflow)
1. Set Roboflow env vars in `backend/.env`.
2. From `backend/` run:
   - `python -m scripts.train_yolo`

If the scan confidence is below the threshold, the API returns a `needs_label` response. The frontend prompts for a label and saves the image to `data/training/labels/<label>/` for future retraining.

## Frontend

1. `cd frontend`
2. `npm install`
3. `npm run dev`

## API Summary
- `POST /api/inventory/scan` (multipart image upload)
- `POST /api/inventory/label` (manual label after low confidence)
- `POST /api/inventory` (manual add)
- `GET /api/inventory`
- `GET /api/inventory/{id}`
- `PATCH /api/inventory/{id}`
- `POST /api/inventory/{id}/expiration`
- `GET /api/inventory/reminders?days=7`
- `POST /api/chat`
