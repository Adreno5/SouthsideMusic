import json

import ncm
import ncm.apis

with open('config.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

ncm.writeLoginInfo(data['login_status'])
ncm.setCurrentSession(ncm.loadSessionFromString(data['session']))

with ncm.getCurrentSession():
    with open('res.json', 'w') as f:
        f.write(
            json.dumps(
                ncm.apis.track.getComments('1388960663', 0, 20),
                indent=4,
            )
        )
