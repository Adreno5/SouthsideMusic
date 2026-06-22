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

import os
import psutil

def mb(value: int | float) -> float:
    return round(value / 1024 / 1024, 2)

p = psutil.Process(os.getpid())

info = p.memory_info()
try:
    full = p.memory_full_info()
except psutil.Error:
    full = info

rss = info.rss
vms = info.vms
working_set = getattr(info, "wset", info.rss)       # Windows: working set
private_bytes = getattr(info, "private", None)      # Windows: private bytes
uss = getattr(full, "uss", None)                    # unique set size

print({
    "pid": p.pid,
    "rss_mb": mb(rss),
    "vms_mb": mb(vms),
    "working_set_mb": mb(working_set),
    "private_bytes_mb": mb(private_bytes) if private_bytes is not None else None,
    "uss_mb": mb(uss) if uss is not None else None,
})