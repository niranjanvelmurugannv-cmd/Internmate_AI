"""InternMate AI API. Local-demo defaults; configure external integrations in backend/.env."""
from __future__ import annotations
import json, os, secrets, smtplib, ssl, traceback
from datetime import datetime, date, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import create_engine, String, Integer, Date, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
DATA_DIR.mkdir(exist_ok=True); UPLOAD_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")
DB_URL = f"sqlite:///{BASE_DIR / 'internmate.db'}"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase): pass
class User(Base):
    __tablename__='users'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    name: Mapped[str]=mapped_column(String(120))
    email: Mapped[str]=mapped_column(String(180),unique=True,index=True)
    password: Mapped[str]=mapped_column(String(255))
    role: Mapped[str]=mapped_column(String(20))
class Project(Base):
    __tablename__='projects'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    intern_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    title: Mapped[str]=mapped_column(String(200))
    description: Mapped[str]=mapped_column(Text)
    requirements: Mapped[str]=mapped_column(Text)
    deadline: Mapped[date]=mapped_column(Date)
    meet_link: Mapped[str]=mapped_column(String(500),default='')
    meeting_at: Mapped[Optional[datetime]]=mapped_column(DateTime,nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    intern: Mapped[User]=relationship()
class Task(Base):
    __tablename__='tasks'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    project_id: Mapped[int]=mapped_column(ForeignKey('projects.id'))
    title: Mapped[str]=mapped_column(String(200))
    status: Mapped[str]=mapped_column(String(30),default='Not Started')
    due_date: Mapped[Optional[date]]=mapped_column(Date,nullable=True)
class Submission(Base):
    __tablename__='submissions'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    project_id: Mapped[int]=mapped_column(ForeignKey('projects.id'))
    intern_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    filename: Mapped[str]=mapped_column(String(255))
    stored_name: Mapped[str]=mapped_column(String(255))
    description: Mapped[str]=mapped_column(Text,default='')
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
class Review(Base):
    __tablename__='reviews'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    project_id: Mapped[int]=mapped_column(ForeignKey('projects.id'))
    summary: Mapped[str]=mapped_column(Text)
    score: Mapped[int]=mapped_column(Integer)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
class LiveSession(Base):
    __tablename__='live_sessions'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    project_id: Mapped[int]=mapped_column(ForeignKey('projects.id'))
    intern_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    status: Mapped[str]=mapped_column(String(30),default='recording')
    notes_json: Mapped[str]=mapped_column(Text,default='[]')
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]]=mapped_column(DateTime,nullable=True)

Base.metadata.create_all(engine)
app=FastAPI(title='InternMate AI API',version='2.0.0',description='AI internship assignment, evaluation, email and Google Calendar/Meet automation.')
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in os.getenv('CORS_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(',')],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
TOKENS: dict[str,int]={}
GOOGLE_FLOW_STATE: dict[str,object]={}
ALLOWED_EXT={'.py','.js','.jsx','.ts','.tsx','.html','.css','.json','.txt','.md','.pdf','.ppt','.pptx','.zip','.png','.jpg','.jpeg'}

class LoginIn(BaseModel): email:str; password:str
class ProjectIn(BaseModel):
    intern_email:str
    title:str=Field(min_length=3,max_length=200)
    description:str
    requirements:str
    deadline:date
    meet_link:str=''
class TaskIn(BaseModel): status:str
class ChatIn(BaseModel): message:str; language:str='English'
class ReviewIn(BaseModel): project_id:int; notes:str=''
class AssignmentPrompt(BaseModel):
    intern_email:str
    role_or_track:str=Field(min_length=2,max_length=120)
    skill_level:str='Beginner'
    project_theme:str=Field(min_length=3,max_length=500)
    deadline:date
    meeting_at:Optional[datetime]=None
    language:str='English'
    send_email:bool=True
class ScheduleIn(BaseModel): meeting_at:datetime; duration_minutes:int=Field(default=30,ge=15,le=180)
class LiveFinishIn(BaseModel): next_meeting_at:Optional[datetime]=None; auto_create_next_task:bool=True


def get_db():
    db=SessionLocal()
    try: yield db
    finally: db.close()

def current_user(authorization:Optional[str]=Header(default=None,alias='Authorization'),db:Session=Depends(get_db)):
    token=authorization[7:].strip() if authorization and authorization.lower().startswith('bearer ') else None
    uid=TOKENS.get(token or '')
    if not uid: raise HTTPException(401,'Please log in again. Sign in again after restarting the backend.')
    user=db.get(User,uid)
    if not user: raise HTTPException(401,'User session not found.')
    return user

def admin_only(user:User):
    if user.role!='admin': raise HTTPException(403,'Administrator access required.')

def owned_project(project_id:int,user:User,db:Session):
    p=db.get(Project,project_id)
    if not p: raise HTTPException(404,'Project not found.')
    if user.role!='admin' and p.intern_id!=user.id: raise HTTPException(403,'You cannot access this project.')
    return p

def gemini_client():
    key=os.getenv('GEMINI_API_KEY','').strip()
    if not key: raise HTTPException(503,'Gemini is not configured. Add GEMINI_API_KEY to backend/.env and restart the backend.')
    try:
        from google import genai
        return genai.Client(api_key=key)
    except Exception as e: raise HTTPException(503,f'Gemini SDK could not initialize: {type(e).__name__}')


def ai_text(prompt: str, model: Optional[str] = None) -> str:
    client = gemini_client()
    try:
        response = client.models.generate_content(
            model=model or os.getenv('GEMINI_MODEL', 'gemini-3.5-flash'),
            contents=prompt
        )
        text = getattr(response, 'text', None)
        if not text:
            raise ValueError('Empty model response')
        return text.strip()
    except HTTPException:
        raise
    except Exception as e:
        print(f"Gemini error details: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(
            502,
            f'Gemini request failed ({type(e).__name__}). Check backend terminal logs.'
        )

def send_email(to_email:str,subject:str,body:str)->dict:
    host=os.getenv('SMTP_HOST','').strip(); user=os.getenv('SMTP_USER','').strip(); password=os.getenv('SMTP_PASSWORD','').strip()
    if not host or not user or not password:
        return {'sent':False,'reason':'Email is not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD and SMTP_FROM in backend/.env.'}
    try:
        msg=EmailMessage(); msg['Subject']=subject; msg['From']=os.getenv('SMTP_FROM',user); msg['To']=to_email; msg.set_content(body)
        port=int(os.getenv('SMTP_PORT','587'))
        if port==465:
            with smtplib.SMTP_SSL(host,port,context=ssl.create_default_context(),timeout=20) as server:
                server.login(user,password); server.send_message(msg)
        else:
            with smtplib.SMTP(host,port,timeout=20) as server:
                server.starttls(context=ssl.create_default_context()); server.login(user,password); server.send_message(msg)
        return {'sent':True,'reason':'Email sent.'}
    except Exception as e: return {'sent':False,'reason':f'Email delivery failed ({type(e).__name__}). Check SMTP settings.'}

def google_credentials_path(): return Path(os.getenv('GOOGLE_OAUTH_CLIENT_FILE',str(BASE_DIR/'credentials.json')))
def google_token_path(): return DATA_DIR/'google_token.json'
def google_service():
    token_path=google_token_path()
    if not token_path.exists(): raise HTTPException(503,'Google Calendar is not connected. Use Integrations → Connect Google first.')
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        creds=Credentials.from_authorized_user_file(str(token_path),['https://www.googleapis.com/auth/calendar.events'])
        if creds.expired and creds.refresh_token:
            creds.refresh(Request()); token_path.write_text(creds.to_json(),encoding='utf-8')
        if not creds.valid: raise ValueError('Google credentials expired')
        return build('calendar','v3',credentials=creds,cache_discovery=False)
    except HTTPException: raise
    except Exception as e: raise HTTPException(503,f'Google Calendar credentials error ({type(e).__name__}). Reconnect Google.')

def create_meet_event(title:str,description:str,start:datetime,end:datetime,attendee:str):
    service=google_service()
    event={'summary':title,'description':description,'start':{'dateTime':start.isoformat(),'timeZone':os.getenv('TIMEZONE','Asia/Kolkata')},'end':{'dateTime':end.isoformat(),'timeZone':os.getenv('TIMEZONE','Asia/Kolkata')},'attendees':[{'email':attendee}], 'conferenceData':{'createRequest':{'requestId':secrets.token_urlsafe(12),'conferenceSolutionKey':{'type':'hangoutsMeet'}}}}
    try:
        created=service.events().insert(calendarId='primary',body=event,conferenceDataVersion=1,sendUpdates='all').execute()
        meet=created.get('hangoutLink') or next((e.get('uri') for e in created.get('conferenceData',{}).get('entryPoints',[]) if e.get('entryPointType')=='video'), '')
        print("Google Meet link:", repr(meet), flush=True)
        return created,meet
    except Exception as e: raise HTTPException(502,f'Google Calendar event creation failed ({type(e).__name__}). Check Calendar API and OAuth permissions.')

def project_json(p:Project,db:Session):
    tasks=db.query(Task).filter_by(project_id=p.id).order_by(Task.id).all(); done=sum(t.status=='Completed' for t in tasks)
    subs=db.query(Submission).filter_by(project_id=p.id).order_by(Submission.created_at.desc()).all(); reviews=db.query(Review).filter_by(project_id=p.id).order_by(Review.created_at.desc()).all()
    return {'id':p.id,'intern_id':p.intern_id,'intern_name':p.intern.name,'intern_email':p.intern.email,'title':p.title,'description':p.description,'requirements':p.requirements,'deadline':p.deadline.isoformat(),'meet_link':p.meet_link,'meeting_at':p.meeting_at.isoformat() if p.meeting_at else None,'tasks':[{'id':t.id,'title':t.title,'status':t.status,'due_date':t.due_date.isoformat() if t.due_date else None} for t in tasks],'progress':round(done/len(tasks)*100) if tasks else 0,'submissions':[{'id':s.id,'filename':s.filename,'description':s.description,'created_at':s.created_at.isoformat()} for s in subs],'reviews':[{'id':r.id,'summary':r.summary,'score':r.score,'created_at':r.created_at.isoformat()} for r in reviews]}

def seed():
    db=SessionLocal()
    try:
        if not db.query(User).filter_by(email='admin@internmate.demo').first():
            admin=User(name='Aarav Sharma',email='admin@internmate.demo',password='admin123',role='admin')
            intern=User(name='Priya Kumar',email='intern@internmate.demo',password='intern123',role='intern')
            db.add_all([admin,intern]); db.commit(); db.refresh(intern)
            p=Project(intern_id=intern.id,title='E-Commerce Customer Segmentation',description='Analyze customer purchase behavior and group customers into meaningful segments to support targeted marketing.',requirements='1. Clean and explore the dataset\n2. Perform exploratory data analysis\n3. Apply K-Means clustering\n4. Visualize and explain customer segments\n5. Submit a README with setup and findings',deadline=date.today()+timedelta(days=7))
            db.add(p); db.commit(); db.refresh(p)
            for title in ['Prepare and clean the dataset','Perform exploratory data analysis','Build K-Means clustering model','Create visualizations and README']:
                db.add(Task(project_id=p.id,title=title,due_date=p.deadline))
            db.commit()
    finally: db.close()
seed()

@app.get('/api/health')
def health():
    return {'status':'ok','app':'InternMate AI','version':'2.0.0','integrations':{'gemini':bool(os.getenv('GEMINI_API_KEY')),'email':bool(os.getenv('SMTP_HOST') and os.getenv('SMTP_USER') and os.getenv('SMTP_PASSWORD')),'google_calendar':google_token_path().exists(),'live_evaluation':'browser audio chunks + Gemini audio analysis'}}
@app.get('/api/integrations/status')
def integration_status(user:User=Depends(current_user)):
    return {'gemini':bool(os.getenv('GEMINI_API_KEY')),'email':bool(os.getenv('SMTP_HOST') and os.getenv('SMTP_USER') and os.getenv('SMTP_PASSWORD')),'google_calendar':google_token_path().exists(),'live_evaluation':bool(os.getenv('GEMINI_API_KEY')),'notes':'Google Meet live audio access is not provided by creating a meeting link alone. This app evaluates microphone audio captured in its own presentation page.'}
@app.post('/api/login')
def login(payload:LoginIn,db:Session=Depends(get_db)):
    user=db.query(User).filter_by(email=payload.email.lower().strip(),password=payload.password).first()
    if not user: raise HTTPException(401,'Incorrect email or password.')
    token=secrets.token_urlsafe(32); TOKENS[token]=user.id
    return {'token':token,'user':{'id':user.id,'name':user.name,'email':user.email,'role':user.role}}
@app.post('/api/logout')
def logout(authorization:Optional[str]=Header(default=None,alias='Authorization')):
    if authorization and authorization.lower().startswith('bearer '): TOKENS.pop(authorization[7:].strip(),None)
    return {'ok':True}
@app.get('/api/me')
def me(user:User=Depends(current_user)): return {'id':user.id,'name':user.name,'email':user.email,'role':user.role}
@app.get('/api/dashboard')
def dashboard(user:User=Depends(current_user),db:Session=Depends(get_db)):
    q=db.query(Project) if user.role=='admin' else db.query(Project).filter_by(intern_id=user.id)
    projects=q.order_by(Project.created_at.desc()).all(); all_projects=db.query(Project).all()
    return {'user':{'name':user.name,'role':user.role},'stats':{'interns':db.query(User).filter_by(role='intern').count(),'active_projects':len(all_projects),'pending_reviews':sum(1 for p in all_projects if not db.query(Review).filter_by(project_id=p.id).first()),'submissions':db.query(Submission).count()},'projects':[project_json(p,db) for p in projects]}
@app.get('/api/interns')
def interns(user:User=Depends(current_user),db:Session=Depends(get_db)):
    admin_only(user); return [{'id':u.id,'name':u.name,'email':u.email} for u in db.query(User).filter_by(role='intern').all()]
@app.post('/api/projects')
def create_project(payload:ProjectIn,background_tasks:BackgroundTasks,user:User=Depends(current_user),db:Session=Depends(get_db)):
    admin_only(user); intern=db.query(User).filter_by(email=payload.intern_email.lower().strip(),role='intern').first()
    if not intern: raise HTTPException(404,'Intern email not found. Create the intern account first.')
    p=Project(intern_id=intern.id,title=payload.title,description=payload.description,requirements=payload.requirements,deadline=payload.deadline,meet_link=payload.meet_link)
    db.add(p); db.commit(); db.refresh(p)
    for line in payload.requirements.splitlines():
        line=line.strip().lstrip('0123456789.-) ').strip()
        if line: db.add(Task(project_id=p.id,title=line,due_date=p.deadline))
    db.commit()
    background_tasks.add_task(send_email,intern.email,f'New internship assignment: {p.title}',f"Hi {intern.name},\n\nYou have a new project assignment.\n\nProject: {p.title}\nSummary: {p.description}\nRequirements:\n{p.requirements}\nSubmission deadline: {p.deadline}\n\nMeet link: {p.meet_link or 'Meeting link will be shared separately.'}\n\n— InternMate AI")
    return project_json(p,db)

@app.post('/api/assignments/generate')
def generate_assignment(
    payload: AssignmentPrompt,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db)
):
    admin_only(user)

    intern = db.query(User).filter_by(
        email=payload.intern_email.lower().strip(),
        role='intern'
    ).first()

    if not intern:
        raise HTTPException(404, 'Candidate email was not found.')

    # Get this intern's previous assignments
    existing_projects = (
        db.query(Project.title, Project.description)
        .filter(Project.intern_id == intern.id)
        .all()
    )

    previous_topics = "\n".join(
        f"- {project.title}: {project.description}"
        for project in existing_projects
    ) or "No previous assignments."

    # Generate a personalized assignment
    prompt = f'''Create a personalized internship project assignment.

Return valid JSON only with these keys:
title, description, requirements (array of 4-7 concise actionable task strings),
deliverables (array), evaluation_criteria (array), suggested_questions (array).

INTERN DETAILS
Name: {intern.name}
Track: {payload.role_or_track}
Skill level: {payload.skill_level}
Preferred theme: {payload.project_theme}
Deadline: {payload.deadline}
Language: {payload.language}

PREVIOUS ASSIGNMENTS FOR THIS INTERN
{previous_topics}

INSTRUCTIONS
1. Generate a fresh, practical topic relevant to the intern's track.
2. Avoid reusing or closely imitating previous assignment topics.
3. Match the difficulty to the intern's skill level.
4. Include 4-7 clear, achievable requirements.
5. Define concrete deliverables and evaluation criteria.
6. Make the project suitable for an internship.
7. Return JSON only, without Markdown fences or extra text.
'''

    raw = ai_text(prompt)

    try:
        cleaned = raw[raw.find('{'):raw.rfind('}') + 1]
        spec = json.loads(cleaned)

        required_keys = [
            'title',
            'description',
            'requirements',
            'deliverables',
            'evaluation_criteria',
            'suggested_questions'
        ]

        if not all(key in spec for key in required_keys):
            raise ValueError('Missing assignment fields.')

        if not isinstance(spec['requirements'], list):
            raise ValueError('Requirements must be a list.')

        if not 4 <= len(spec['requirements']) <= 7:
            raise ValueError('Expected 4 to 7 requirements.')

        if not spec['title'] or not spec['description']:
            raise ValueError('Title and description are required.')

        # Check for an exact title duplicate across all interns
        duplicate = db.query(Project).filter(
            Project.title.ilike(str(spec['title']).strip())
        ).first()

        if duplicate:
            retry_prompt = prompt + f'''

IMPORTANT: The generated title "{spec['title']}" already exists.
Generate a different topic and title. Do not copy this existing title.
Return valid JSON only with the same required keys.
'''
            raw = ai_text(retry_prompt)
            cleaned = raw[raw.find('{'):raw.rfind('}') + 1]
            spec = json.loads(cleaned)

            if db.query(Project).filter(
                Project.title.ilike(str(spec['title']).strip())
            ).first():
                raise HTTPException(
                    409,
                    'AI generated a duplicate title. Please generate again.'
                )

        requirements = '\n'.join(
            f'{i + 1}. {item}'
            for i, item in enumerate(spec['requirements'])
        )

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            502,
            'Gemini did not return valid assignment JSON. Please try again.'
        )

    # Save assignment for the selected intern
    project = Project(
        intern_id=intern.id,
        title=str(spec['title']).strip()[:200],
        description=str(spec['description']),
        requirements=requirements,
        deadline=payload.deadline
    )

    db.add(project)
    db.commit()
    db.refresh(project)

    # Create tasks from the assignment requirements
    for item in spec['requirements']:
        db.add(
            Task(
                project_id=project.id,
                title=str(item)[:200],
                due_date=project.deadline
            )
        )

    db.commit()

    # Send the assignment email if requested
    email_result = {
        'sent': False,
        'reason': 'Email not requested.'
    }

    if payload.send_email:
        email_body = f"""Hi {intern.name},

Your personalized AI-generated project assignment is ready.

PROJECT DETAILS
Title: {project.title}
Description: {project.description}

REQUIREMENTS
{requirements}

DELIVERABLES
{chr(10).join('- ' + str(item) for item in spec.get('deliverables', []))}

SUBMISSION DEADLINE
{project.deadline}

EVALUATION CRITERIA
{chr(10).join('- ' + str(item) for item in spec.get('evaluation_criteria', []))}

Please complete the tasks and submit your work through InternMate AI.

Regards,
InternMate AI
"""

        email_result = send_email(
            intern.email,
            f'Your project assignment: {project.title}',
            email_body
        )

    return {
        'project': project_json(project, db),
        'deliverables': spec.get('deliverables', []),
        'evaluation_criteria': spec.get('evaluation_criteria', []),
        'suggested_questions': spec.get('suggested_questions', []),
        'email': email_result
    }

@app.patch('/api/tasks/{task_id}')
def update_task(task_id:int,payload:TaskIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    if payload.status not in ['Not Started','In Progress','Completed','Needs Improvement']: raise HTTPException(400,'Invalid task status.')
    task=db.get(Task,task_id)
    if not task: raise HTTPException(404,'Task not found.')
    owned_project(task.project_id,user,db); task.status=payload.status; db.commit(); return {'ok':True,'status':task.status}
@app.post('/api/projects/{project_id}/upload')
def upload(project_id:int,description:str='',file:UploadFile=File(...),user:User=Depends(current_user),db:Session=Depends(get_db)):
    p=owned_project(project_id,user,db); ext=Path(file.filename or '').suffix.lower()
    if ext not in ALLOWED_EXT: raise HTTPException(400,'File type not allowed.')
    content=file.file.read(15*1024*1024+1)
    if len(content)>15*1024*1024: raise HTTPException(413,'File exceeds 15 MB limit.')
    stored=f'{user.id}_{project_id}_{secrets.token_hex(6)}{ext}'; (UPLOAD_DIR/stored).write_bytes(content)
    s=Submission(project_id=p.id,intern_id=user.id,filename=Path(file.filename).name,stored_name=stored,description=description[:1000]); db.add(s); db.commit(); db.refresh(s)
    return {'ok':True,'submission':{'id':s.id,'filename':s.filename,'created_at':s.created_at.isoformat()}}
@app.get('/api/projects/{project_id}/files/{submission_id}')
def download(project_id:int,submission_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from fastapi.responses import FileResponse
    owned_project(project_id,user,db); s=db.get(Submission,submission_id)
    if not s or s.project_id!=project_id: raise HTTPException(404,'Submission not found.')
    path=UPLOAD_DIR/s.stored_name
    if not path.exists(): raise HTTPException(404,'File no longer exists.')
    return FileResponse(path,filename=s.filename)
@app.post('/api/evaluate/{project_id}')
def evaluate(project_id:int,payload:ReviewIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    p=owned_project(project_id,user,db); admin_only(user)
    tasks=db.query(Task).filter_by(project_id=p.id).all(); subs=db.query(Submission).filter_by(project_id=p.id).all(); done=sum(t.status=='Completed' for t in tasks); score=round(done/len(tasks)*100) if tasks else 0
    prompt=f'''Write a constructive internship project review. Be explicit that checklist status and filenames are not proof of working code. Project: {p.title}. Summary: {p.description}. Requirements: {p.requirements}. Task statuses: {[(t.title,t.status) for t in tasks]}. Submission filenames/descriptions: {[(s.filename,s.description) for s in subs]}. HR observations: {payload.notes or 'None supplied'}. Provide strengths, gaps, next steps and a note to verify with live demo/tests. Do not claim to have opened or executed submitted files.'''
    try: summary=ai_text(prompt)
    except HTTPException: summary=f'Checklist-based draft review for {p.title}. Progress score: {score}% ({done}/{len(tasks)} tasks marked complete). HR notes: {payload.notes or "No notes supplied"}. AI review was unavailable; verify project through a live demo and tests before final approval.'
    r=Review(project_id=p.id,summary=summary,score=score); db.add(r); db.commit(); db.refresh(r)
    return {'id':r.id,'score':score,'summary':summary,'created_at':r.created_at.isoformat()}
@app.post('/api/ai/test')
def test_gemini(payload:ChatIn,user:User=Depends(current_user)):
    answer=ai_text('You are InternMate AI, an internship management assistant. Reply concisely to this test message: '+payload.message)
    return {'success':True,'answer':answer,'model':os.getenv('GEMINI_MODEL','gemini-2.5-flash')}

@app.post('/api/chat')
def chat(payload:ChatIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    q=db.query(Project) if user.role=='admin' else db.query(Project).filter_by(intern_id=user.id); p=q.order_by(Project.created_at.desc()).first()
    if not p: return {'answer':'No project is assigned yet. Please contact your HR administrator.','language':payload.language,'mode':'project-aware AI'}
    tasks=db.query(Task).filter_by(project_id=p.id).all(); done=sum(t.status=='Completed' for t in tasks)
    prompt=f'''You are InternMate AI, a supportive internship mentor. Reply in {payload.language}. User: {user.name} ({user.role}). Project: {p.title}. Summary: {p.description}. Requirements: {p.requirements}. Deadline: {p.deadline}. Tasks/status: {[(t.title,t.status) for t in tasks]}. Progress: {done}/{len(tasks)}. Meet link: {p.meet_link or 'not scheduled'}. Answer the user's question using this context and do not invent completed work or scheduled events. User message: {payload.message}'''
    try: answer=ai_text(prompt)
    except HTTPException:
        answer=f"Hi {user.name}! Project: {p.title}. Deadline: {p.deadline}. Requirements:\n{p.requirements}\n\nGemini is not configured or unavailable right now, so I'm showing project context instead."
    return {'answer':answer,'language':payload.language,'mode':'Gemini project-aware assistant' if os.getenv('GEMINI_API_KEY') else 'project-context fallback'}
@app.get('/api/reviews/{project_id}')
def reviews(project_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    owned_project(project_id,user,db); return [{'id':r.id,'score':r.score,'summary':r.summary,'created_at':r.created_at.isoformat()} for r in db.query(Review).filter_by(project_id=project_id).order_by(Review.created_at.desc()).all()]

@app.get('/api/integrations/google/connect')
def google_connect(user:User=Depends(current_user)):
    admin_only(user); path=google_credentials_path()
    if not path.exists(): raise HTTPException(400,f'Google OAuth client file not found: {path.name}. Download OAuth client JSON from Google Cloud Console and save it as backend/credentials.json.')
    try:
        from google_auth_oauthlib.flow import Flow
        flow=Flow.from_client_secrets_file(str(path),scopes=['https://www.googleapis.com/auth/calendar.events'],redirect_uri=os.getenv('GOOGLE_REDIRECT_URI','http://127.0.0.1:8000/api/integrations/google/callback'))
        state=secrets.token_urlsafe(18); GOOGLE_FLOW_STATE[state]=flow
        url,_=flow.authorization_url(access_type='offline',include_granted_scopes='true',prompt='consent',state=state)
        return {'authorization_url':url,'message':'Open authorization_url in a browser and grant Calendar access.'}
    except Exception as e: raise HTTPException(500,f'Could not start Google OAuth ({type(e).__name__}).')
@app.get('/api/integrations/google/callback')
def google_callback(code:str,state:str,error:Optional[str]=None):
    if error: return RedirectResponse('http://localhost:5173/?google=denied')
    flow=GOOGLE_FLOW_STATE.pop(state,None)
    if not flow: raise HTTPException(400,'Google OAuth state expired. Start the connection again.')
    try:
        flow.fetch_token(code=code); creds=flow.credentials; google_token_path().write_text(creds.to_json(),encoding='utf-8')
        return RedirectResponse('http://localhost:5173/?google=connected')
    except Exception as e: raise HTTPException(400,f'Google OAuth callback failed ({type(e).__name__}). Check redirect URI settings.')

@app.post('/api/projects/{project_id}/schedule-meeting')
def schedule_meeting(
    project_id: int,
    payload: ScheduleIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db)
):
    admin_only(user)
    p = owned_project(project_id, user, db)

    if payload.meeting_at.replace(tzinfo=None) < datetime.utcnow():
        raise HTTPException(400, 'Meeting time must be in the future.')

    created, meet = create_meet_event(
        f'InternMate AI project explanation: {p.title}',
        f'Candidate presentation for {p.title}.\n'
        f'{p.description}\nRequirements:\n{p.requirements}',
        payload.meeting_at,
        payload.meeting_at + timedelta(minutes=payload.duration_minutes),
        p.intern.email
    )

    p.meeting_at = payload.meeting_at
    p.meet_link = meet or created.get('htmlLink', '')
    db.commit()

    mail_body = f"""Hi {p.intern.name},

Your project explanation meeting has been scheduled successfully.

PROJECT DETAILS
Project: {p.title}
Description: {p.description}
Requirements:
{p.requirements}

SUBMISSION DETAILS
Deadline: {p.deadline}

MEETING DETAILS
Date and time: {payload.meeting_at.strftime('%d %B %Y, %I:%M %p')}
Duration: {payload.duration_minutes} minutes
Google Meet link: {p.meet_link}

PREPARATION INSTRUCTIONS
1. Review the project description and requirements.
2. Prepare your approach for each requirement.
3. Keep your questions ready for the discussion.
4. Complete and submit your work before the deadline.

Please join using the Google Meet link at the scheduled time.

Regards,
InternMate AI
"""

    mail = send_email(
        p.intern.email,
        f'Meeting Scheduled: {p.title}',
        mail_body
    )

    return {
        'event_id': created.get('id'),
        'meet_link': p.meet_link,
        'meeting_at': p.meeting_at.isoformat(),
        'email': mail
    }

@app.post('/api/live-evaluation/{project_id}/start')
def live_start(project_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    p=owned_project(project_id,user,db)
    session=LiveSession(project_id=p.id,intern_id=user.id,notes_json='[]'); db.add(session); db.commit(); db.refresh(session)
    return {'session_id':session.id,'project':p.title,'message':'Live evaluation started. The browser will upload microphone audio chunks for AI feedback.'}
@app.post('/api/live-evaluation/{project_id}/{session_id}/chunk')
def live_chunk(project_id:int,session_id:int,file:UploadFile=File(...),user:User=Depends(current_user),db:Session=Depends(get_db)):
    p=owned_project(project_id,user,db); session=db.get(LiveSession,session_id)
    if not session or session.project_id!=p.id or session.intern_id!=user.id: raise HTTPException(404,'Live session not found.')
    if session.status!='recording': raise HTTPException(400,'This live session has already finished.')
    data=file.file.read(6*1024*1024+1)
    if not data: raise HTTPException(400,'Empty audio chunk.')
    if len(data)>6*1024*1024: raise HTTPException(413,'Audio chunk exceeds 6 MB.')
    try:
        from google.genai import types
        client=gemini_client()
        mime=file.content_type or 'audio/webm'
        prompt=f'''You are providing real-time coaching during an internship project presentation. Analyze this short audio segment from the candidate's presentation about project {p.title}. Project brief: {p.description}. Requirements: {p.requirements}. Return concise JSON only with keys: heard_summary (what you can reasonably infer from audio; say unclear if audio is unintelligible), feedback (one useful immediate coaching note), question (one relevant follow-up question to ask the candidate, or empty string). Do not infer identity, protected traits, or grade voice/accent. Assess only technical content. This is an imperfect audio segment, not the whole presentation.'''
        response=client.models.generate_content(model=os.getenv('GEMINI_MODEL','gemini-2.5-flash'),contents=[prompt,types.Part.from_bytes(data=data,mime_type=mime)])
        raw=(response.text or '').strip();
        try:
            cleaned=raw[raw.find('{'):raw.rfind('}')+1]; result=json.loads(cleaned)
        except Exception: result={'heard_summary':raw[:1200],'feedback':'Explain the technical choices and evidence more explicitly.','question':'What result or test best demonstrates this part of the project?'}
        notes=json.loads(session.notes_json or '[]'); notes.append({'at':datetime.utcnow().isoformat(),'heard_summary':result.get('heard_summary',''),'feedback':result.get('feedback',''),'question':result.get('question','')}); session.notes_json=json.dumps(notes,ensure_ascii=False); db.commit()
        return {'success':True,'feedback':notes[-1]}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502,f'Audio evaluation failed ({type(e).__name__}). Check Gemini configuration and try a longer audio segment.')
@app.post('/api/live-evaluation/{project_id}/{session_id}/finish')
def live_finish(project_id:int,session_id:int,payload:LiveFinishIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    p=owned_project(project_id,user,db); session=db.get(LiveSession,session_id)
    if not session or session.project_id!=p.id or session.intern_id!=user.id: raise HTTPException(404,'Live session not found.')
    notes=json.loads(session.notes_json or '[]')
    if not notes: raise HTTPException(400,'No audio segments were evaluated. Record and send at least one segment first.')
    tasks=db.query(Task).filter_by(project_id=p.id).all()
    prompt=f'''Create a fair presentation feedback report for {user.name}'s project explanation. Project: {p.title}. Brief: {p.description}. Requirements: {p.requirements}. Audio segment summaries and feedback from AI: {notes}. Provide overall feedback, strengths, gaps, recommended next task, and 3 concrete improvement steps. Mention audio analysis may be incomplete and HR should review. Do not invent evidence. Keep the report email-friendly.'''
    try: summary=ai_text(prompt)
    except HTTPException: summary='Presentation feedback\n\n'+ '\n'.join(f"Segment {i+1}: {n.get('heard_summary')}\nFeedback: {n.get('feedback')}" for i,n in enumerate(notes))
    review=Review(project_id=p.id,summary=summary,score=min(100,max(0,60+min(40,len(notes)*5)))); db.add(review)
    next_task_result=None
    if payload.auto_create_next_task:
        try:
            generated=ai_text(f'''Based on this internship project and presentation feedback, suggest exactly one next learning task. Return JSON only: {{"title":"...","description":"...","deadline_days":integer 2 to 7}}. Current project: {p.title}. Current requirements: {p.requirements}. Feedback: {summary}. Make it actionable and a reasonable next step.''')
            spec=json.loads(generated[generated.find('{'):generated.rfind('}')+1]); days=max(2,min(7,int(spec.get('deadline_days',3))))
            task=Task(project_id=p.id,title=str(spec['title'])[:200],status='Not Started',due_date=date.today()+timedelta(days=days)); db.add(task)
            next_task_result={'title':task.title,'deadline':task.due_date.isoformat(),'description':spec.get('description','')}
        except Exception: pass
    session.status='completed'; session.finished_at=datetime.utcnow(); db.commit(); db.refresh(review)
    email_body=f"Hi {user.name},\n\nYour presentation feedback for {p.title}:\n\n{summary}\n\n"
    if next_task_result: email_body+=f"Your next task: {next_task_result['title']}\nDetails: {next_task_result['description']}\nDeadline: {next_task_result['deadline']}\n\n"
    email_body+='Please discuss any concerns with your HR reviewer. AI feedback can be incomplete and should be reviewed by a human.\n\n— InternMate AI'
    email=send_email(user.email,f'Presentation feedback: {p.title}',email_body)
    next_meeting=None
    if payload.next_meeting_at:
        try:
            created,meet=create_meet_event(f'Next project explanation: {p.title}',f'Follow-up presentation for {p.title}.',payload.next_meeting_at,payload.next_meeting_at+timedelta(minutes=30),p.intern.email)
            p.meeting_at=payload.next_meeting_at; p.meet_link=meet or created.get('htmlLink',''); db.commit(); next_meeting={'meet_link':p.meet_link,'meeting_at':p.meeting_at.isoformat()}
            email_body+=f'\nNext explanation meeting: {p.meeting_at.isoformat()}\nMeet link: {p.meet_link}\n'
        except Exception: next_meeting={'error':'Meeting not scheduled. Connect Google Calendar and retry.'}
    return {'review_id':review.id,'feedback':summary,'next_task':next_task_result,'email':email,'next_meeting':next_meeting,'note':'Audio-based feedback is assistive and must not be the sole basis for hiring or internship decisions.'}
@app.get('/api/live-evaluation/{project_id}/history')
def live_history(project_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    owned_project(project_id,user,db); sessions=db.query(LiveSession).filter_by(project_id=project_id).order_by(LiveSession.created_at.desc()).all()
    if user.role!='admin': sessions=[s for s in sessions if s.intern_id==user.id]
    return [{'id':s.id,'status':s.status,'created_at':s.created_at.isoformat(),'finished_at':s.finished_at.isoformat() if s.finished_at else None,'segments':json.loads(s.notes_json or '[]')} for s in sessions]
