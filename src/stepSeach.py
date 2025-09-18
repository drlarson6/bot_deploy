from flask import Flask, request, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

def get_sheet_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('path_to_your_service_account.json', scope)
    client = gspread.authorize(creds)
    return client

def get_step_info(step_number, client):
    sheet = client.open('Your Google Sheet Name').sheet1
    records = sheet.get_all_records()
    for record in records:
        if record['Step Number'] == step_number:
            return record['Step Description'], record['SOP Link']
    return None, None

@app.route('/webhook', methods=['POST'])
def webhook():
    req = request.get_json(silent=True, force=True)
    intent_name = req['queryResult']['intent']['displayName']
    
    if intent_name == 'GetStepInfo':  # Ensure this matches your Dialogflow intent
        step_number = req['queryResult']['parameters']['step_number']
        client = get_sheet_client()
        step_description, sop_link = get_step_info(step_number, client)
        
        if step_description:
            fulfillment_text = f"Step {step_number}: {step_description}. The relevant SOP can be found at {sop_link}. Do you need help with anything else?"
        else:
            fulfillment_text = "I'm sorry, I couldn't find information for that step number. Can I help you with something else?"
        
        response = {
            "fulfillmentText": fulfillment_text
        }
    else:
        fulfillment_text = "This intent is not handled."
        response = {
            "fulfillmentText": fulfillment_text
        }
    
    return jsonify(response)

if __name__ == '__main__':
    app.run(debug=True)
