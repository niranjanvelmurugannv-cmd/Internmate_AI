# InternMate AI — AI Internship Management & Evaluation

A local full-stack application built from the original React + FastAPI + SQLite MVP. It adds Gemini-powered assignment generation, project-aware AI chat/review, SMTP email delivery, Google Calendar/Meet event creation, and a browser microphone-based presentation evaluation flow.

> **Important scope note:** Google Meet event creation does not give an application direct access to the Meet call's audio. This project captures the candidate's microphone in the InternMate AI page (with permission) and sends short audio chunks to Gemini for coaching. For true in-meeting capture, you would need an additional approved Google Meet media integration / Workspace setup. AI feedback can be wrong and must not be the sole basis for hiring, grading, or internship decisions.

## Included

- HR and intern demo accounts; project/task management; file uploads and review history.
- Gemini assignment generator that creates a project summary, actionable checklist, deliverables and evaluation criteria.
- Gemini-powered project-aware assistant and evaluation drafts.
- SMTP email for new assignments, meeting notifications and final presentation feedback.
- Google OAuth connection flow; creates Calendar events with Google Meet conference links.
- Candidate presentation page with browser microphone capture, incremental audio analysis, coaching notes, follow-up questions, and a final feedback report.
- Option to generate the candidate's next task automatically after the presentation report.
- Integration status page and clear messages when an external service is not configured.

## Requirements

- Windows 10/11, macOS, or Linux
- Python 3.10+ (Python 3.11 or 3.12 recommended)
- Node.js 20+ and npm
- A Gemini API key from <https://aistudio.google.com/apikey>
- Optional: SMTP credentials for sending email
- Optional: Google Cloud OAuth client for Calendar/Meet creation

## Quick start — Windows PowerShell

Extract the ZIP, then open PowerShell in the extracted `internmate-ai` folder.

### 1. Configure backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Set `GEMINI_API_KEY` to your new key. Do not put the key in React files, screenshots, GitHub, or this chat. If a key was previously exposed, revoke it and create a new one.

Start the API:

```powershell
python -m uvicorn main:app --reload
```

API docs: <http://127.0.0.1:8000/docs>

### 2. Configure frontend in a second terminal

From the extracted project root:

```powershell
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

### 3. Demo accounts

- HR: `admin@internmate.demo` / `admin123`
- Candidate: `intern@internmate.demo` / `intern123`

These accounts and plaintext passwords are for local demonstration only. Do not use them for a public deployment.

## Gemini setup

1. Create an API key at <https://aistudio.google.com/apikey>.
2. Save it only in `backend/.env` as `GEMINI_API_KEY=...`.
3. Keep `GEMINI_MODEL=gemini-2.5-flash` unless that model is unavailable to your account; use a model supported by your Gemini API access.
4. Restart FastAPI after changing `.env`.

Gemini is used for assignment generation, project-aware chat, review drafting, audio-segment feedback, and final feedback/next-task suggestions. API availability, quotas, model support and billing/limits depend on your Google account.

## SMTP email setup (optional but required for actual delivery)

For Gmail, enable 2-Step Verification and create a Google **App Password**. Do not use your regular Google password. In `backend/.env`, configure:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-sender@gmail.com
SMTP_PASSWORD=your-google-app-password
SMTP_FROM=your-sender@gmail.com
```

Restart FastAPI after editing `.env`. Without SMTP configuration, features still run where possible but return a status explaining that email was not sent. Check `Automation center` for integration status.

## Google Calendar and Google Meet setup (optional)

1. Open <https://console.cloud.google.com/> and create/select a Google Cloud project.
2. Enable **Google Calendar API**.
3. Configure the OAuth consent screen. For a personal test, add your Google account as a test user if the app is in testing mode.
4. Create an OAuth client ID of type **Web application**.
5. Add this exact authorized redirect URI:
   `http://127.0.0.1:8000/api/integrations/google/callback`
6. Download the OAuth client JSON and save it as `backend/credentials.json` (same folder as `main.py`). Never commit this file.
7. Set in `backend/.env`:

```env
GOOGLE_OAUTH_CLIENT_FILE=credentials.json
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/api/integrations/google/callback
TIMEZONE=Asia/Kolkata
```

8. Restart the backend, sign in as HR, open **Automation center**, and click **Connect Google Calendar**. Complete Google's consent screen. Return to InternMate AI and refresh integration status.
9. Select a project and schedule a future meeting. Google Calendar creates an event and requests a Meet conference link; delivery of the link can take a short time.

The OAuth token is saved under `backend/data/google_token.json`. Keep the entire `backend/data` folder private and excluded from Git.

## Automatic assignment and review workflow

1. Sign in as HR.
2. Open **Automation center**.
3. Fill candidate email, role/track, skill level, project theme and deadline.
4. Click **Generate & assign project**. Gemini generates the brief and tasks, saves them to SQLite and attempts email delivery if SMTP is configured.
5. Connect Google and schedule a project explanation session to create a Calendar event/Meet link and email details.
6. Candidate signs in, opens Automation center, allows microphone access, and clicks **Start live evaluation** while explaining the project. The page submits audio chunks for Gemini feedback. Click **Stop recording**, wait for any in-flight segment to finish, then click **Finish & generate feedback report**.
7. The system saves the feedback, attempts to email it, and tries to generate one next task. HR should review the result and schedule a follow-up session as needed.

### Live evaluation limitations

- Audio capture happens in this web page, not directly inside Google Meet. The candidate must grant browser microphone permission and should be clearly informed that audio is sent to Gemini.
- Browser audio chunking and model responses can vary by browser, microphone, network and model access. The feature is a prototype; test it with short recordings first.
- The final score is a lightweight progress indicator, not a validated assessment score. Human review is required.
- Next tasks are added to the same project checklist for this prototype. Production workflow should model assignment rounds, reviewer approvals, calendar retries, and audit history explicitly.

## API overview

- `POST /api/login`, `POST /api/logout`, `GET /api/me`
- `GET /api/dashboard`, `GET /api/interns`, `POST /api/projects`, `PATCH /api/tasks/{task_id}`
- `POST /api/ai/generate-assignment`
- `POST /api/chat`, `POST /api/evaluate/{project_id}`, `GET /api/reviews/{project_id}`
- `GET /api/integrations/status`, `GET /api/integrations/google/connect`, `GET /api/integrations/google/callback`
- `POST /api/projects/{project_id}/schedule-meeting`
- `POST /api/live-evaluation/{project_id}/start`
- `POST /api/live-evaluation/{project_id}/{session_id}/chunk`
- `POST /api/live-evaluation/{project_id}/{session_id}/finish`

## Troubleshooting

- **401 Unauthorized:** Sign in again in the app. API tokens are stored in memory and reset when FastAPI restarts; old tokens become invalid.
- **Gemini 503 / missing key:** Check `backend/.env`, ensure the key name is exactly `GEMINI_API_KEY`, then restart the backend.
- **Gemini 502:** Check API key validity, model access, quota, network and backend terminal output.
- **Email not sent:** Check SMTP fields and use an app password where required. The response explains whether email is unconfigured or failed.
- **Google connect says credentials missing:** Put the downloaded OAuth client JSON at `backend/credentials.json`.
- **Google redirect mismatch:** The Google Cloud authorized redirect URI must exactly match the URI in `.env`.
- **Microphone not available:** Use a modern browser (Chrome/Edge recommended), allow microphone access, and close other apps using the microphone.
- **Frontend cannot reach API:** Keep both terminals running and use `http://127.0.0.1:8000` for backend and `http://localhost:5173` for frontend.

## Security and deployment warning

This is an integration-rich **local prototype**, not production-ready. It uses demo accounts and simple passwords, in-memory sessions, local SQLite, and local file storage. Before handling real candidates or deploying publicly, add password hashing, durable signed sessions, authorization and audit tests, secure file scanning/storage, rate limiting, background job retries, consent/retention controls for audio, secrets management, HTTPS, strict CORS, database migrations, and operational monitoring. Never commit `.env`, `credentials.json`, `data/`, `uploads/`, or the SQLite database.
