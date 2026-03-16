from flask import Flask, jsonify, request
from services import scan_emails, send_email_notification, get_unpaid_bills, supabase, VERCEL_URL

app = Flask(__name__)

@app.route('/')
def home():
    return "Bill Scanner Online."

@app.route('/api/cron/scan', methods=['GET'])
def trigger_scan():
    new_bills = scan_emails(query="subject:(bill OR statement OR invoice) is:unread")
    
    for bill in new_bills:
        pay_link = f"{VERCEL_URL}/api/mark_paid/{bill['id']}"
        subject = f"🧾 New Bill: {bill['subject']}"
        
        html_body = f"""
        <h2>New Bill Detected</h2>
        <p><strong>Amount:</strong> {bill['amount']}</p>
        <p><strong>Due Date:</strong> {bill['due_date']}</p>
        <br>
        <a href="{pay_link}" style="padding: 10px 20px; background-color: #28a745; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">
            Mark as Paid ✅
        </a>
        """
        send_email_notification(subject, html_body)
        
    return jsonify({"status": "success", "processed": len(new_bills)})

@app.route('/api/cron/remind', methods=['GET'])
def trigger_reminders():
    unpaid = get_unpaid_bills()
    for bill in unpaid:
        pay_link = f"{VERCEL_URL}/api/mark_paid/{bill['id']}"
        subject = f"⚠️ REMINDER: {bill['subject']} is Due!"
        
        html_body = f"""
        <h2 style="color: #dc3545;">Bill Reminder</h2>
        <p>Your bill for <strong>{bill['amount']}</strong> is due on <strong>{bill['due_date']}</strong>.</p>
        <br>
        <a href="{pay_link}" style="padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">
            I just paid this! ✅
        </a>
        """
        send_email_notification(subject, html_body)
        
    return jsonify({"status": "success", "reminders": len(unpaid)})

@app.route('/api/manual_backward_scan', methods=['GET'])
def backward_scan():
    """Run this manually in your browser: /api/manual_backward_scan?after=2026/01/01"""
    after_date = request.args.get('after', '2026/01/01')
    query = f"subject:(bill OR statement OR invoice) after:{after_date}"
    historical_bills = scan_emails(query=query)
    return jsonify({"status": "success", "historical_bills_added": len(historical_bills)})

@app.route('/api/mark_paid/<int:bill_id>', methods=['GET'])
def mark_paid(bill_id):
    try:
        supabase.table('bills').update({'status': 'paid'}).eq('id', bill_id).execute()
        return f"""
        <div style="font-family: sans-serif; text-align: center; margin-top: 50px;">
            <h1 style="color: #28a745;">Success! 🎉</h1>
            <p>Bill #{bill_id} has been marked as paid in your database.</p>
            <p>You can close this tab.</p>
        </div>
        """
    except Exception as e:
        return f"Database error: {e}"

if __name__ == '__main__':
    app.run(port=5000, debug=True)