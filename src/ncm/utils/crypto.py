# -*- coding: utf-8 -*-
"""Essential implementations of some of netease's security algorithms"""

from . import _hex_digest, _hash_hex_digest
from .aes import AES

# region secrets
EAPI_DIGEST_SALT = 'nobody%(url)suse%(text)smd5forencrypt'
EAPI_DATA_SALT = '%(url)s-36cd479b6b5-%(text)s-36cd479b6b5-%(digest)s'
EAPI_AES_KEY = 'e82ckenh8dichen8'  # ecb
# endregion


# region Cryptographic algorithims
def _pkcs7_pad(data, bs=AES.BLOCKSIZE):
    return data + (bs - len(data) % bs) * chr(bs - len(data) % bs)


def _pkcs7_unpad(data, bs=AES.BLOCKSIZE):
    pad = data[-1]
    if pad not in range(0, bs):
        return data  # hack : data isn't padded
    return data[:-pad]


def _aes_encrypt(data: str, key: str, iv='', mode=AES.MODE_CBC):
    cipher = AES(key.encode())
    if mode == AES.MODE_CBC:
        return cipher.encrypt_cbc_nopadding(_pkcs7_pad(data).encode(), iv.encode())
    else:
        return cipher.encrypt_ecb_nopadding(_pkcs7_pad(data).encode())


def _aes_decrypt(data: str, key: str, iv='', mode=AES.MODE_CBC):
    cipher = AES(key.encode())
    if isinstance(data, str):
        raw = data.encode()
    else:
        raw = data
    if mode == AES.MODE_CBC:
        return _pkcs7_unpad(cipher.decrypt_cbc_nopadding(raw, iv.encode()))
    else:
        return _pkcs7_unpad(cipher.decrypt_ecb_nopadding(raw))




# endregion


# region api-specific crypto routines


def _eapi_encrypt(url, params):
    """Implements EAPI request encryption"""
    url, params = str(url), str(params)
    digest = _hash_hex_digest(EAPI_DIGEST_SALT % {'url': url, 'text': params})
    params = EAPI_DATA_SALT % ({'url': url, 'text': params, 'digest': digest})
    return {
        'params': _hex_digest(_aes_encrypt(params, key=EAPI_AES_KEY, mode=AES.MODE_ECB))
    }


def _eapi_decrypt(cipher):
    """Implements EAPI response decryption"""
    cipher = bytearray(cipher) if isinstance(cipher, str) else cipher  # type: ignore
    return _aes_decrypt(cipher, EAPI_AES_KEY, mode=AES.MODE_ECB) if cipher else cipher  # type: ignore




# endregion
