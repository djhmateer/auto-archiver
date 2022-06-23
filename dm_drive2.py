from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

def main():
    SCOPES = ['https://www.googleapis.com/auth/drive']

    creds = service_account.Credentials.from_service_account_file('service_account.json', scopes=SCOPES)
        
    try:
        service = build('drive', 'v3', credentials=creds)

        # 1. Call the Drive v3 API to get files
        results = service.files().list().execute()
        items = results.get('files', [])

        print('Files:')
        for item in items:
            print(u'{0} ({1})'.format(item['name'], item['id']))

        
    except HttpError as error:
        # TODO(developer) - Handle errors from drive API.
        print(f'An error occurred: {error}')


if __name__ == '__main__':
    main()
    
# if not items:
#             print('No files found.')
#             return

