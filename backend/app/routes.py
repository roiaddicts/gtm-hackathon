from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, Field

import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.apps import meet_v2

import base64
from email.message import EmailMessage

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ['https://www.googleapis.com/auth/meetings.space.created','https://www.googleapis.com/auth/gmail.send']

router = APIRouter()


class ApiStatus(BaseModel):
    status: str
    message: str
    timestamp: datetime

class ScheduleCreate(BaseModel):
    email: str
    detail:str
    timestamp:datetime

class ScheduleResponse(BaseModel):
    id: str
    status: bool
    url_meet: str
    received_at: datetime

class LeadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    company: str | None = Field(default=None, max_length=160)
    message: str | None = Field(default=None, max_length=1000)


class LeadResponse(BaseModel):
    id: str
    status: str
    received_at: datetime



@router.get("/status", response_model=ApiStatus, tags=["api"])
async def status() -> ApiStatus:
    return ApiStatus(
        status="ready",
        message="FastAPI backend is ready for the Lovable UI.",
        timestamp=datetime.now(timezone.utc),
    )


@router.post("/leads", response_model=LeadResponse, status_code=201, tags=["leads"])
async def create_lead(lead: LeadCreate) -> LeadResponse:
    # Replace this with persistence or CRM handoff once the hackathon flow is set.
    return LeadResponse(
        id=str(uuid4()),
        status="received",
        received_at=datetime.now(timezone.utc),
    )

@router.post("/calendar",response_model=ScheduleResponse,status_code=201,tags=["shedule"])
async def asing_calendar(schedule: ScheduleCreate) -> ScheduleResponse:
    creds=None
    if  os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    try:
        client = meet_v2.SpacesServiceClient(credentials=creds)
        request = meet_v2.CreateSpaceRequest()
        response = client.create_space(request=request)
        print(f'Space created: {response.meeting_uri}')
        
    except Exception as error:
        # TODO(developer) - Handle errors from Meet API.
        print(f'An error occurred: {error}')
   
    service = build('gmail', 'v1', credentials=creds)
    message = EmailMessage()
    message.set_content(f'Hola! Por favor accede al meeting asignado {response.meeting_uri}')
    message['To'] = schedule.email
    message['From'] = 'me'
    message['Subject'] = 'PRUEBA HUB'

    # Encode message
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    create_message = {'raw': encoded_message}
    
    try:
        sent_msg = service.users().messages().send(userId="me", body=create_message).execute()
        print(f'Email sent! Message ID: {sent_msg["id"]} {schedule.email}')
    except Exception as e:
        print(f'An error occurred: {e}')
    return ScheduleResponse(
        id=str(uuid4()),
        status=True,
        url_meet=response.meeting_uri,
        received_at=datetime.now(timezone.utc),
)
