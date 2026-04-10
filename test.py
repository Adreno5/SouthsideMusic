import json
from pprint import pprint
import uuid

from pyncm import apis

with open('res.json', 'w', encoding='utf-8') as f:
    f.write(json.dumps(apis.login.LoginViaAnonymousAccount(deviceId=uuid.uuid4().hex), indent=4))