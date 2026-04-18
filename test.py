import json
from pprint import pprint
import uuid

from pyncm import apis

apis.login.LoginViaAnonymousAccount(session=apis.GetCurrentSession())
with apis.GetCurrentSession():
    with open('res.json', 'w', encoding='utf-8') as f:
        apis.login.LoginViaEmail('fjc_0331@163.com', 'Wy771105', session=apis.GetCurrentSession())
        f.write(json.dumps(apis.GetCurrentSession(), indent=4))