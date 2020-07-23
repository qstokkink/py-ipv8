from binascii import hexlify

from nacl.bindings.crypto_box import crypto_box_SECRETKEYBYTES
from nacl.bindings.crypto_sign import crypto_sign_BYTES, crypto_sign_SEEDBYTES
from nacl.encoding import HexEncoder
from nacl.public import PublicKey as PubKey
from nacl.signing import VerifyKey


from ...keyvault.keys import PublicKey


class LibNaCLPK(PublicKey):
    """
    A LibNaCL implementation of a public key.
    """

    def __init__(self, binarykey="", pk=None, hex_vk=None):
        """
        Create a new LibNaCL public key. Optionally load it from a string representation or
        using a public key and verification key.

        :param binarykey: load the pk from this string (see key_to_bin())
        :param pk: the libnacl public key to use in byte format
        :param hex_vk: a verification key in hex format
        """
        # Load the key, if specified
        if binarykey:
            pk, vk = (binarykey[:crypto_box_SECRETKEYBYTES],
                      binarykey[crypto_box_SECRETKEYBYTES: crypto_box_SECRETKEYBYTES
                                + crypto_sign_SEEDBYTES])
            hex_vk = hexlify(vk)
        # Construct the public key and verifier objects
        self.key = PubKey(pk)
        self.veri = VerifyKey(hex_vk, encoder=HexEncoder())

    def verify(self, signature, msg):
        """
        Verify whether a given signature is correct for a message.

        :param signature: the given signature
        :param msg: the given message
        """
        return self.veri.verify(signature + msg)

    def key_to_bin(self):
        """
        Get the string representation of this key.
        """
        return b"LibNaCLPK:" + bytes(self.key) + bytes(self.veri)

    def get_signature_length(self):
        """
        Returns the length, in bytes, of each signature made using EC.
        """
        return crypto_sign_BYTES
