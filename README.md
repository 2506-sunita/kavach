# Kavach - Personal Safety Shield
One tap or "Kavach Alert" sends an SOS with live location, a message and a voice note to trusted contacts.

- backend/   FastAPI server (also serves the mobile web app from frontend/)
- frontend/  Mobile web app (PWA)
- dashboard/ Streamlit live dashboard

Run locally:
  pip install -r backend/requirements.txt
  cd backend && python -m uvicorn main:app --host 0.0.0.0 --port 8000
  pip install -r dashboard/requirements.txt
  cd dashboard && python -m streamlit run app.py

Deploy: backend on Render (build: pip install -r backend/requirements.txt, start: cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT),
dashboard on Streamlit Community Cloud (main file: dashboard/app.py, secret: KAVACH_API = "<render url>").
Env vars on Render: ADMIN_KEY (required), DEBUG_OTP (1 for demo, 0 for real use), TWILIO_SID / TWILIO_TOKEN / TWILIO_FROM (optional).
