# CyberGPT

An evidence-grounded cybersecurity assistant (capstone project).
It separates what is known from what is guessed, and works in modes:
Learn, Analyze, Pentest, Defend, Report.

## Run it on your computer

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env        # then edit .env (see the comments inside)
python -m pytest              # all tests should pass
uvicorn app.main:app --reload # open http://127.0.0.1:8000
```

## Settings

All settings live in `.env` and start with `CYBERGPT_` (see `.env.example`).
Never commit `.env`: it contains your API key.

## Hosting

Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
Health check address: `/healthz`
Set `CYBERGPT_ACCESS_PASSWORD` on any public copy.

Only test systems you are authorized to assess.