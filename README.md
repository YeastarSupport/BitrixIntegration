# BitrixIntegration

This integration app runs on Windows platform only
This integration can only used for Bitrix24 to integrate with S series PBX(Hardware only)
This integration use works for PBX Model S50,S100,S300

## Configuration
- The config.txt file must be in the same foler with bitrixintegration.exe

**In config.txt**
- **pbx url** the url of the PBX
- **bitrix_basic_url** Bitrix24 inbound webhook，the url is the webhook to call REST API, need to assign permissions: Users,Telephony,CRM
- **api_username** S series PBX API username
- **api_password** S series PBX API password

**In Bitrix24**
- in the profile->contact information: set Internal Phone property to the PBX extension number
- Internal Phone must be the number of the PBX extension, the application will use it to bind the CRM user with the PBX extension

### Example
- pbx_url:https://192.168.29.101:8088
- bitrix_basic_url:https://b24-u42rxz.bitrix24.com/rest/1/oyifaryt8vs7b868/
- api_username:api
- api_password:S1sBf24v

