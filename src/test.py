import json

import pyncm
import pyncm.apis

with open('config.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

pyncm.writeLoginInfo(data['login_status'])
pyncm.setCurrentSession(pyncm.loadSessionFromString(data['session']))

with pyncm.getCurrentSession():
    with open('res.json', 'w') as f:
        f.write(json.dumps(pyncm.apis.login.loginRefreshToken(), indent=4))
