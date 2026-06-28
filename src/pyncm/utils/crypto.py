# -*- coding: utf-8 -*-
"""Essential implementations of some of netease's security algorithms"""

import base64
from . import _hex_compose, _hex_digest, _hash_hex_digest, _random_string, security
from .aes import AES

# region secrets
WEAPI_RSA_PUBKEY = (
    int(
        '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7',
        16,
    ),
    int('10001', 16),  # textbook rsa without padding
)
# AES keys & IVs targets AES-128 mode
WEAPI_AES_KEY = '0CoJUm6Qyw8W8jud'  # cbc
WEAPI_AES_IV = '0102030405060708'  # cbc
LINUXAPI_AES_KEY = 'rFgB&h#%2?^eDg:Q'  # ecb
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


def _rsa_encrypt(data: str, n, e, reverse=True):
    plain = data if not reverse else ''.join(reversed(data))
    m, E, N = int(''.join(plain).encode('utf-8').hex(), 16), e, n
    r = pow(m, E, N)
    return _hex_compose(hex(r)[2:].zfill(256))


# endregion


# region api-specific crypto routines
def _weapi_encrypt(params, aes_key2=None):
    """Implements /weapi/ Asymmetric encryption"""
    aes_key2 = aes_key2 or _random_string(16)
    params = str(params)
    # 1st go,encrypt the text with aes_key and aes_iv
    params = str(
        base64.encodebytes(
            _aes_encrypt(
                data=params, key=WEAPI_AES_KEY, iv=WEAPI_AES_IV, mode=AES.MODE_CBC
            )
        ),
        encoding='utf-8',
    )
    # 2nd go,encrypt the ENCRYPTED text again,with the 2nd key and aes_iv
    params = str(
        base64.encodebytes(
            _aes_encrypt(data=params, key=aes_key2, iv=WEAPI_AES_IV, mode=AES.MODE_CBC)
        ),
        encoding='utf-8',
    )
    # 3rd go,generate RSA encrypted encSecKey
    encSecKey = _hex_digest(_rsa_encrypt(aes_key2, *WEAPI_RSA_PUBKEY))
    return {'params': params, 'encSecKey': encSecKey}


def _abroad_decrypt(result):
    """Decrypts 'abroad:True' messages"""
    return security.c_decrypt_abroad_message(result)


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


def _linux_api_encrypt(params):
    """Implements Linux/Deepin client API encryption"""
    params = str(params)
    return {
        'eparams': _hex_digest(
            _aes_encrypt(params, key=LINUXAPI_AES_KEY, mode=AES.MODE_ECB)
        )
    }


# endregion
