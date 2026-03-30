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
    
    if not creds.valid and creds.refresh_token:
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

def parse_batch_with_gemini(emails_list):
    """Processes multiple emails in ONE API call with robust model fallbacks."""
    if not emails_list:
        return []

    # Fallback cascade: Tries newest preview, then stable, then older fast models
    models_to_try = [
        'gemini-3.1-flash-lite-preview',
        'gemini-3.1-flash-lite',
        'gemini-2.5-flash',
        'gemini-2.0-flash'
    ]
    
    prompt = """
    Extract the bill amount and due date for each email snippet below.
    Return ONLY a valid JSON array of objects. 
    Each object MUST have the keys: "message_id", "amount", and "due_date".
    Example: [{"message_id": "18a2b", "amount": "$50.00", "due_date": "10/15/2026"}]
    If missing, use null.
    
    Emails:
    """
    for e in emails_list:
        prompt += f"\nMessage ID: {e['id']}\nText: {e['snippet']}\n---\n"
        
    last_error = None
    
    for model_name in models_to_try:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            clean_text = response.text.strip().replace('```json', '').replace('```', '')
            return json.loads(clean_text)
        except Exception as e:
            print(f"Model {model_name} failed: {e}")
            last_error = e
            continue # Try the next model in the list
            
    # If the loop finishes and all models failed, do NOT fail silently!
    raise Exception(f"All Gemini models failed. Last error: {last_error}")

def scan_emails(query="subject:(bill OR statement OR invoice OR HOA OR dues) is:unread"):
    """Searches Gmail, batches them into 1 Gemini call, saves to DB."""
    service = get_gmail_service()
    
    results = service.users().messages().list(userId='me', q=query, maxResults=40).execute()
    messages = results.get('messages', [])
    
    if not messages:
        return []

    emails_to_parse = []
    bills_found = []
    
    for msg in messages:
        msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
        headers = msg_data['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown Bill')
        snippet = msg_data.get('snippet', '')
        
        emails_to_parse.append({
            'id': msg['id'],
            'subject': subject,
            'snippet': snippet
        })
        
    # This will now raise an error if it fails, which app.py will catch
    parsed_results = parse_batch_with_gemini(emails_to_parse)
    
    for data in parsed_results:
        msg_id = data.get('message_id')
        amount = data.get('amount')
        due_date = data.get('due_date')
        
        if msg_id and amount and due_date:
            subject = next((e['subject'] for e in emails_to_parse if e['id'] == msg_id), 'Unknown Bill')
            
            bill_data = {
                'message_id': msg_id,
                'subject': subject,
                'amount': amount,
                'due_date': due_date,
                'status': 'unpaid'
            }
            try:
                res = supabase.table('bills').insert(bill_data).execute()
                bill_data['id'] = res.data[0]['id']
                bills_found.append(bill_data)
            except Exception:
                pass 
                
            try:
                service.users().messages().modify(userId='me', id=msg_id, body={'removeLabelIds': ['UNREAD']}).execute()
            except Exception:
                pass
            
    return bills_found

def get_unpaid_bills():
    return supabase.table('bills').select('*').eq('status', 'unpaid').execute().data