# import json

# import pyncm
# import pyncm.apis

# with open('config.json', 'r', encoding='utf-8') as f:
#     data = json.load(f)

# pyncm.writeLoginInfo(data['login_status'])
# pyncm.setCurrentSession(pyncm.loadSessionFromString(data['session']))

# with pyncm.getCurrentSession():
#     with open('res.json', 'w') as f:
#         f.write(json.dumps(pyncm.apis.track.getTrackDetail(['518904426']), indent=4))

import threading, psutil

cores = psutil.cpu_count(logical=False) or 1
print(cores)

def compute():
    i = 0
    while True:
        i += 1

threads = [threading.Thread(target=compute) for _ in range(cores)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()