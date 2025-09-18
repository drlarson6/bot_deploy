from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from email.mime.text import MIMEText
import base64
import secrets
import string
import os, json
from google.oauth2.credentials import Credentials


# ... other imports ...

# Define the SCOPES
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/gmail.send']
from googleapiclient.discovery import build

# Load credentials and create a service object


token_env = os.environ.get("GMAIL_TOKEN_JSON")
if token_env:
    creds = Credentials.from_authorized_user_info(json.loads(token_env), SCOPES)
else:
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)  # local dev fallback

#creds = Credentials.from_authorized_user_file('token.json', SCOPES)


service = build('sheets', 'v4', credentials=creds)

# ... rest of your script ...
# Import necessary libraries
# ... other imports ...

# Define your spreadsheet ID and range name here
spreadsheet_id = '1lhFA6qcIohjkktNVXPqnNgocn7XfkOgU6CCzvaY1cPk'
range_name = 'Operators!A2:C31'

#new code
#search_name = 'Doug'
#end new code

# Function to generate a random new_r_code
def generate_random_r_code(length):
    # Define the characters that can be used in the new_r_code
    characters = string.ascii_letters + string.digits + string.punctuation
    # Generate a random new_r_code
    new_r_code = ''.join(secrets.choice(characters) for i in range(length))
    return new_r_code

# Specify the length of the new_r_code
r_code_length = 8  # You can choose any length

# Function to write back to the spreadsheet
def update_sheet(service, spreadsheet_id, range_name, value):
    body = {
        'values': [value]
    }
    result = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=range_name,
        valueInputOption='USER_ENTERED', body=body).execute()
    print(f"{result.get('updatedCells')} cells updated.")


# Assuming 'service' is defined and authorized elsewhere in your script
# Now you can call your function with the service and spreadsheet ID
#New code

# ... other imports and setup ...

def create_message(sender, to, subject, message_text):
    message = MIMEText(message_text)
    message['to'] = to
    message['from'] = sender
    message['subject'] = subject
    raw_message = base64.urlsafe_b64encode(message.as_bytes())
    return {
        'raw': raw_message.decode('utf-8')
    }

def send_email(service, user_id, message):
    try:
        message = (service.users().messages().send(userId=user_id, body=message).execute())
        print('Message Id: %s' % message['id'])
        return message
    except Exception as e:#
        print(f'An error occurred: {e}')
        return None

def read_sheet_and_send_email(spreadsheet_id, range_name, userID, email_to):
    #print(userID)

    # Use the Sheets API to get the values from the specified range
    sheet_service = build('sheets', 'v4', credentials=creds)
    result = sheet_service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_name).execute()
    values = result.get('values', [])

   
    user_found = False  # Flag to indicate if the user ID was found   
    new_r_code = None
    for i, row in enumerate(values, start=2): 
        if len(row) > 0 and row[1] == userID:  # search_name
            user_found = True  # Set the flag to True when user is found
            print(i)
            print(userID)

            new_r_code = generate_random_r_code(r_code_length)
            update_range = f"Operators!C{i}"  # Use the enumeration index directly
            update_sheet(service, spreadsheet_id, update_range, [new_r_code])
            print(f"The new random password for {userID} is {new_r_code}")
            break
    if not user_found:
        print(f"{userID} not found.")
        return {"userID": userID, "newPassword": new_r_code}

    if new_r_code:
        gmail_service = build('gmail', 'v1', credentials=creds)
        message = create_message('drlarson6@gmail.com', email_to, 'Retrieval Code', f'The new random password for {userID} is {new_r_code}')

        #print(new_r_code)

        send_email(gmail_service, 'me', message)


# --- minimal add for creating a new Sheet + URL ---
def create_google_sheet_min(title: str):
    sheets_service = build('sheets', 'v4', credentials=creds)
    resp = sheets_service.spreadsheets().create(
        body={"properties": {"title": title}}
    ).execute()
    spreadsheet_id = resp["spreadsheetId"]
    sheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    return spreadsheet_id, sheet_url

def append_rows(spreadsheet_id: str, rows: list[list], range_: str = "Sheet1!A:Z"):
    """Append rows to a Google Sheet."""
    sheets_service = build('sheets', 'v4', credentials=creds)
    return sheets_service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=range_,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows}
    ).execute()