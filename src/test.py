import json

import pyncm
import pyncm.apis

with open('config.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

pyncm.WriteLoginInfo(data['login_status'])
pyncm.SetCurrentSession(pyncm.LoadSessionFromString(data['session']))

with pyncm.GetCurrentSession():
    with open('res.json', 'w') as f:
        # f.write(json.dumps(pyncm.apis.playlist., indent=4))
        ...