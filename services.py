import os
import json
import base64
from email.message import EmailMessage
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import google.generativeai as genai
from supabase import create_client, Client as SupabaseClient

# --- ENVIRONMENT VARIABLES ---
MY_EMAIL = os.environ.get('MY_EMAIL')
VERCEL_URL = os.environ.get('VERCEL_URL', 'http://localhost:5000').rstrip('/')

# --- INITIALIZE CLIENTS ---
supabase: SupabaseClient = create_client(os.environ.get('SUPABASE_URL'), os.environ.get('SUPABASE_KEY'))
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

def get_gmail_service():
    """Authenticates Gmail. Auto-refreshes the token if expired."""
    creds_dict = json.loads(os.environ.get('GMAIL_TOKEN_JSON'))
    creds = Credentials.from_authorized_user_info(creds_dict)
    
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        
    return build('gmail', 'v1', credentials=creds)

def send_email_notification(subject, body_html):
    """Sends an HTML email to yourself via Gmail API."""
    service = get_gmail_service()
    message = EmailMessage()
    message.set_content("Please enable HTML to view this message.")
    message.add_alternative(body_html, subtype='html')
    
    message['To'] = MY_EMAIL
    message['From'] = MY_EMAIL
    message['Subject'] = subject

    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    try:
        service.users().messages().send(userId="me", body={'raw': encoded_message}).execute()
        print(f"Notification sent: {subject}")
    except Exception as e:
        print(f"Failed to send email: {e}")

def parse_with_gemini(text):
    """Robust JSON extraction using Gemini Flash."""
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
    Extract the bill amount and due date from this text. 
    Return ONLY a valid JSON object with keys "amount" and "due_date".
    Example: {{"amount": "$50.00", "due_date": "10/15/2026"}}
    If missing, use null.
    Text: {text}
    """
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.strip().replace('```json', '').replace('```', '')
        data = json.loads(clean_text)
        return data.get('amount'), data.get('due_date')
    except Exception as e:
        print(f"Gemini Parse Error: {e}")
        return None, None

def scan_emails(query="subject:(bill OR statement OR invoice) is:unread"):
    """Searches Gmail, parses bills, saves to DB, and marks as read."""
    service = get_gmail_service()
    results = service.users().messages().list(userId='me', q=query).execute()
    messages = results.get('messages', [])
    
    bills_found = []
    for msg in messages:
        msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
        headers = msg_data['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown Bill')
        snippet = msg_data.get('snippet', '')
        
        amount, due_date = parse_with_gemini(snippet)
        
        if amount and due_date:
            bill_data = {
                'message_id': msg['id'],
                'subject': subject,
                'amount': amount,
                'due_date': due_date,
                'status': 'unpaid'
            }
            try:
                # Insert into Supabase. Fails gracefully if message_id already exists.
                res = supabase.table('bills').insert(bill_data).execute()
                bill_data['id'] = res.data[0]['id']
                bills_found.append(bill_data)
            except Exception as e:
                print(f"Skipped duplicate or DB error: {e}")
                
            # Remove UNREAD label
            service.users().messages().modify(userId='me', id=msg['id'], body={'removeLabelIds': ['UNREAD']}).execute()
            
    return bills_found

def get_unpaid_bills():
    return supabase.table('bills').select('*').eq('status', 'unpaid').execute().data