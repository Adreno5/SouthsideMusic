# -*- coding: utf-8 -*-
"""Miscellaneous implementations of some of netease's security algorithms
Implemented:
- `cloudmusic_dll_encode_id` for anonymous device ids
"""

from hashlib import md5
from base64 import b64encode

# region secrets
ID_XOR_KEY_1 = b'3go8&$8*3*3h0k(2)2'
# endregion




# region cloudmusic.dll (Windows) security
def cloudmusic_dll_encode_id(some_id):
    # XORs bytes then returns its base64 MD5 hash. Used in encodeAnonymousId
    # Searching for ID_XOR_KEY_1 in cloudmusic.dll will get you to their implementation
    xored = bytearray(
        [
            c ^ ID_XOR_KEY_1[idx % len(ID_XOR_KEY_1)]
            for idx, c in enumerate(some_id.encode())
        ]
    )
    digest = md5(xored).digest()
    return b64encode(digest).decode()


# endregion
