from binascii import hexlify

from nacl.bindings.crypto_box import crypto_box_SECRETKEYBYTES
from nacl.bindings.crypto_sign import crypto_sign_BYTES, crypto_sign_SEEDBYTES
from nacl.public import PrivateKey as PrivKey
from nacl.secret import SecretBox
from nacl.signing import SigningKey

from ...keyvault.keys import PrivateKey
from ...keyvault.public.libnaclkey import LibNaCLPK


class DualSecret(object):

    def __init__(self, crypt=None, seed=None):
        if crypt is None:
            self.sk = PrivKey.generate()
            crypt = bytes(self.sk)
        self.crypt = SecretBox(crypt)
        self.sk = PrivKey(bytes(self.crypt))
        self.pk = self.sk.public_key

        self.signer = SigningKey.generate() if seed is None else SigningKey(seed)
        self.vk = self.signer.verify_key
        self.seed = bytes(self.signer)

    def signature(self, msg):
        return self.signer.sign(msg)[:crypto_sign_BYTES]  # Appends the message


class LibNaCLSK(PrivateKey, LibNaCLPK):
    """
    A LibNaCL implementation of a secret key.
    """

    def __init__(self, binarykey=""):
        """
        Create a new LibNaCL secret key. Optionally load it from a string representation.
        Otherwise generate it from the 25519 curve.

        :param binarykey: load the sk from this string (see key_to_bin())
        """
        # Load the key, if specified
        if binarykey:
            crypt, seed = (binarykey[:crypto_box_SECRETKEYBYTES],
                           binarykey[crypto_box_SECRETKEYBYTES: crypto_box_SECRETKEYBYTES
                                     + crypto_sign_SEEDBYTES])
            self.key = DualSecret(crypt, seed)
        else:
            self.key = DualSecret()
        # Immediately create a verifier
        self.veri = self.key.vk

    def pub(self):
        """
        Get the public key for this secret key.
        """
        return LibNaCLPK(pk=bytes(self.key.pk), hex_vk=hexlify(bytes(self.veri)))

    def signature(self, msg):
        """
        Create a signature for a message.

        :param msg: the message to sign
        :return: the signature for the message
        """
        return self.key.signature(msg)

    def key_to_bin(self):
        """
        Get the string representation of this key.
        """
        return b"LibNaCLSK:" + bytes(self.key.sk) + self.key.seed
