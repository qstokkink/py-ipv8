import struct

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

import libnacl

from ...keyvault.crypto import ECCrypto, LibNaCLPK

try:
    from _evp import ffi, lib
except ImportError:
    from .evp_build import build
    build()
    from _evp import ffi, lib


class CryptoException(Exception):
    pass


class TunnelCrypto(ECCrypto):

    def __init__(self):
        super().__init__()
        self.error_map = {
            1: "Failed to create EVP_CIPHER_CTX",
            2: "Failed to initialize new EVP_chacha20_poly1305 EVP_CIPHER_CTX",
            3: "Failed to set EVP_CIPHER_CTX IV",
            4: "Failed to feed input to cipher",
            5: "Failed to finalize cipher output",
            6: "Failed to construct AEAD tag"
        }

    def initialize(self, key):
        self.key = key
        assert isinstance(self.key, LibNaCLPK), type(self.key)

    def is_key_compatible(self, key):
        return isinstance(key, LibNaCLPK)

    def generate_diffie_secret(self):
        tmp_key = self.generate_key("curve25519")
        X = tmp_key.key.pk

        return tmp_key, X

    def generate_diffie_shared_secret(self, dh_received, key=None):
        if key is None:
            key = self.key

        tmp_key = self.generate_key("curve25519")
        y = tmp_key.key.sk
        Y = tmp_key.key.pk
        shared_secret = libnacl.crypto_box_beforenm(dh_received, y) + libnacl.crypto_box_beforenm(dh_received, key.key.sk)

        AUTH = libnacl.crypto_auth(Y, shared_secret[:32])
        return shared_secret, Y, AUTH

    def verify_and_generate_shared_secret(self, dh_secret, dh_received, auth, B):
        shared_secret = libnacl.crypto_box_beforenm(dh_received, dh_secret.key.sk) + libnacl.crypto_box_beforenm(B, dh_secret.key.sk)
        libnacl.crypto_auth_verify(auth, dh_received, shared_secret[:32])

        return shared_secret

    def generate_session_keys(self, shared_secret):
        hkdf = HKDFExpand(algorithm=hashes.SHA256(), backend=default_backend(), length=72, info=b"key_generation")
        key = hkdf.derive(shared_secret)

        kf = key[:32]
        kb = key[32:64]
        sf = key[64:68]
        sb = key[68:72]
        return [kf, kb, sf, sb, 1, 1]

    def get_session_keys(self, keys, direction):
        # increment salt_explicit
        keys[direction + 4] += 1
        return keys[direction], keys[direction + 2], keys[direction + 4]

    def encrypt_str(self, content, key, salt, salt_explicit):
        """
        return the encrypted content prepended with salt_explicit

        Port of code by @hbiyik:
        https://github.com/hbiyik/evp/blob/master/asyncaead/evp.pyx
        https://github.com/hbiyik/evp/blob/master/asyncaead/evp.pxd
        """
        inputlen = len(content)
        packed_salt_explicit = struct.pack('!q', salt_explicit)
        iv = salt + packed_salt_explicit
        ivlen = len(iv)
        output = ffi.new("unsigned char []", inputlen)
        tag = ffi.new("unsigned char []", 16)
        err = lib.encode(output, inputlen, content, inputlen, key, iv, ivlen, tag)

        if err != 0:
            raise RuntimeError(self.error_map[err])

        return packed_salt_explicit + ffi.buffer(output, inputlen) + ffi.buffer(tag, lib.tls_tag_len)

    def decrypt_str(self, content, key, salt):
        """
        content contains the tag and salt_explicit in plaintext

        Port of code by @hbiyik:
        https://github.com/hbiyik/evp/blob/master/asyncaead/evp.pyx
        https://github.com/hbiyik/evp/blob/master/asyncaead/evp.pxd
        """
        if len(content) < 24:
            raise CryptoException("truncated content")

        inputlen = len(content) - lib.tls_tag_len - 8
        iv = salt + content[:8]
        ivlen = len(iv)
        output = ffi.new("unsigned char []", inputlen)
        tag = content[-lib.tls_tag_len:]
        err = lib.decode(output, inputlen, content[8:], inputlen, key, iv, ivlen, tag)

        if err != 0:
            raise RuntimeError(self.error_map[err])

        return bytes(ffi.buffer(output, inputlen))

