from flask import Flask, jsonify, request
from datetime import datetime, timedelta
import hmac, hashlib, os
from services import scan_emails, send_email_notification, get_unpaid_bills, supabase, VERCEL_URL

app = Flask(__name__)

TOKEN_SECRET = os.environ.get('TOKEN_SECRET', '')

def generate_token(bill_id):
    return hmac.new(TOKEN_SECRET.encode(), str(bill_id).encode(), hashlib.sha256).hexdigest()

def verify_token(bill_id, token):
    expected = generate_token(bill_id)
    return hmac.compare_digest(expected, token)

@app.route('/')
def home():
    # A clean, mobile-friendly HTML dashboard
    html_dashboard = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Bill Scanner Dashboard</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f4f7f6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }
            h1 { text-align: center; color: #2c3e50; }
            .card { background: white; border-radius: 10px; padding: 20px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
            .btn { display: inline-block; width: 100%; box-sizing: border-box; padding: 12px 20px; margin: 8px 0; text-align: center; text-decoration: none; border-radius: 6px; font-weight: bold; border: none; cursor: pointer; font-size: 16px; transition: opacity 0.2s; }
            .btn:hover { opacity: 0.9; }
            .btn-scan { background-color: #28a745; color: white; }
            .btn-remind { background-color: #ffc107; color: #333; }
            .btn-historical { background-color: #007bff; color: white; }
            input[type="date"] { width: 100%; padding: 10px; margin-top: 8px; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 5px; box-sizing: border-box; font-size: 16px; }
            p { margin-top: 0; color: #555; }
        </style>
    </head>
    <body>
        <h1>🧾 Command Center</h1>
        
        <div class="card">
            <h3>Automated Jobs</h3>
            <p>Manually trigger your daily Vercel cron jobs.</p>
            <a href="/api/cron/scan" target="_blank" class="btn btn-scan">🔍 Run Inbox Scan Now</a>
            <a href="/api/cron/remind" target="_blank" class="btn btn-remind">⚠️ Send Reminders Now</a>
        </div>

        <div class="card">
            <h3>Historical Scan</h3>
            <p>Select a date to scan all older emails from that point forward.</p>
            <form action="/api/manual_backward_scan" method="GET" target="_blank">
                <label for="after"><strong>Scan emails after:</strong></label>
                <input type="date" id="after" name="after" value="2026-01-01" required>
                <button type="submit" class="btn btn-historical">⏪ Run Historical Scan</button>
            </form>
        </div>
    </body>
    </html>
    """
    return html_dashboard

@app.route('/api/cron/scan', methods=['GET'])
def trigger_scan():
    try:
        after_date = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y/%m/%d')
        new_bills = scan_emails(query=f"subject:(bill OR statement OR invoice OR HOA OR dues) is:unread after:{after_date}")
        
        for bill in new_bills:
            pay_link = f"{VERCEL_URL}/api/mark_paid/{bill['id']}?token={generate_token(bill['id'])}"
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
        
    except Exception as e:
        error_msg = str(e)
        send_email_notification("⚠️ Bill Scanner Error", f"Your 8 AM scan failed to run. Error details: <br><br>{error_msg}")
        return jsonify({"status": "error", "message": error_msg}), 500

@app.route('/api/cron/remind', methods=['GET'])
def trigger_reminders():
    unpaid = get_unpaid_bills()
    if not unpaid:
        return jsonify({"status": "success", "reminders": 0})

    rows_html = ""
    for bill in unpaid:
        pay_link = f"{VERCEL_URL}/api/mark_paid/{bill['id']}?token={generate_token(bill['id'])}"
        rows_html += f"""
        <tr>
            <td style="padding: 12px 16px; border-bottom: 1px solid #eee;">{bill['subject']}</td>
            <td style="padding: 12px 16px; border-bottom: 1px solid #eee; text-align: center;"><strong>{bill['amount']}</strong></td>
            <td style="padding: 12px 16px; border-bottom: 1px solid #eee; text-align: center;">{bill['due_date']}</td>
            <td style="padding: 12px 16px; border-bottom: 1px solid #eee; text-align: center;">
                <a href="{pay_link}" style="padding: 8px 16px; background-color: #28a745; color: white; text-decoration: none; border-radius: 5px; font-weight: bold; white-space: nowrap;">
                    ✅ Mark Paid
                </a>
            </td>
        </tr>
        """

    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 640px; margin: 0 auto;">
        <h2 style="color: #dc3545;">⚠️ You have {len(unpaid)} unpaid bill{'s' if len(unpaid) > 1 else ''}</h2>
        <table style="width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
            <thead>
                <tr style="background-color: #f8f9fa;">
                    <th style="padding: 12px 16px; text-align: left; color: #555;">Bill</th>
                    <th style="padding: 12px 16px; text-align: center; color: #555;">Amount</th>
                    <th style="padding: 12px 16px; text-align: center; color: #555;">Due Date</th>
                    <th style="padding: 12px 16px; text-align: center; color: #555;">Action</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
    """

    send_email_notification(f"⚠️ Bill Reminder: {len(unpaid)} unpaid bill{'s' if len(unpaid) > 1 else ''}", html_body)
    return jsonify({"status": "success", "reminders": len(unpaid)})

@app.route('/api/manual_backward_scan', methods=['GET'])
def backward_scan():
    # The form automatically formats the date as YYYY-MM-DD which is perfect for Gmail
    after_date = request.args.get('after', '2026-01-01')
    
    # We remove the 'is:unread' tag here so it scans read emails too!
    query = f"subject:(bill OR statement OR invoice OR HOA OR dues) after:{after_date}"
    
    try:
        historical_bills = scan_emails(query=query)
        return jsonify({"status": "success", "historical_bills_added": len(historical_bills)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/mark_paid/<int:bill_id>', methods=['GET'])
def mark_paid(bill_id):
    token = request.args.get('token', '')
    if not verify_token(bill_id, token):
        return "Invalid or missing token.", 403
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