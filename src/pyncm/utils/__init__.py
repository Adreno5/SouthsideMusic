# -*- coding: utf-8 -*-
import random
import time
from hashlib import md5

BASE62 = 'PJArHa0dpwhvMNYqKnTbitWfEmosQ9527ZBx46IXUgOzD81VuSFyckLRljG3eC'
BASE64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'


def _random_string(_l, chars=BASE62):
    # Generates random string of `len` chars within a selected number of chars
    return ''.join([random.choice(chars) for i in range(0, _l)])


def _hex_digest(data: bytearray):
    # Digests a `bytearray` to a hex string
    return ''.join([hex(d)[2:].zfill(2) for d in data])


def _hex_compose(hexstr: str):
    # Composes a hex string back to a `bytearray`
    return bytearray([int(hexstr[i : i + 2], 16) for i in range(0, len(hexstr), 2)])


def _hash_digest(text):
    # Digests 128 bit md5 hash
    HASH = md5(text.encode('utf-8'))
    return HASH.digest()


def _hash_hex_digest(text):
    """Digests 128 bit md5 hash,then digest it as a hexstring"""
    return _hex_digest(_hash_digest(text))


def _generate_s_device_id() -> str:
    """Generate sDeviceId

    This code is from web source code
    """
    random_num = random.randrange(1000000)
    return f'unknown-{random_num}'


def _generate_chain_id(s_device_id: str) -> str:
    """Generate chainId param for web login api

    This code is from web source code
    """
    timestamp = int(time.time() * 1000)
    # the rule is '{version}_{s_device_id}_{platform}_{action}_{timestamp}'
    return f'v1_{s_device_id}_web_login_{timestamp}'
