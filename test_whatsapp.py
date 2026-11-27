"""Checks that direct WhatsApp sending works.

  python test_whatsapp.py PHONE_NUMBER_ID 91XXXXXXXXXX [template] [language] [param1 param2 ...]

Example with Meta's built-in test template (no variables):
  python test_whatsapp.py 123456789012345 919876543210 hello_world en_US
Example with your birthday template:
  python test_whatsapp.py 123456789012345 919876543210 birthday_wish en "Ravi" "you" "Hiral" "98XXXXXXXX"
"""
import sys
from dotenv import load_dotenv
load_dotenv()
import messaging

if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)
phone_id, to = sys.argv[1], sys.argv[2]
name = sys.argv[3] if len(sys.argv) > 3 else "hello_world"
lang = sys.argv[4] if len(sys.argv) > 4 else "en_US"
params = sys.argv[5:]
print("Token set     :", messaging.whatsapp_api_configured())
ok, err = messaging.send_whatsapp_api(to, "", phone_id=phone_id, template=dict(name=name, lang=lang, params=params))
print("RESULT        :", "SENT - check the phone" if ok else "FAILED -> " + err)
