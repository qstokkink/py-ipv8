from __future__ import absolute_import

from unittest import TestCase

from ...keyvault.crypto import default_eccrypto


class TestEncryption(TestCase):

    def setUp(self):
        super(TestEncryption, self).setUp()
        self.key = default_eccrypto.generate_key(u"curve25519")
        self.other_key = default_eccrypto.generate_key(u"curve25519")

    def test_encrypt_normal(self):
        """
        Check if we can encrypt a message for a given public key and decrypt it using its private key.
        """
        message = "hello"
        encrypted = self.key.encrypt(message, self.other_key.pub())
        decrypted = self.other_key.decrypt(encrypted, self.key.pub())

        self.assertEqual(message, decrypted)

    def test_encrypt_empty(self):
        """
        Check if encryptions are never the empty string.
        """
        message = ""
        encrypted = self.key.encrypt(message, self.other_key.pub())
        decrypted = self.other_key.decrypt(encrypted, self.key.pub())

        self.assertFalse(not encrypted) # Should never be an empty string
        self.assertEqual(message, decrypted)

    def test_encrypt_long(self):
        """
        Check if encryptions are properly padded.
        """
        message = "hello" * 50
        encrypted = self.key.encrypt(message, self.other_key.pub())
        encrypted2 = self.key.encrypt(message + "hello", self.other_key.pub())
        decrypted = self.other_key.decrypt(encrypted, self.key.pub())

        self.assertEqual(message, decrypted)
        self.assertEqual(len(encrypted), len(encrypted2)) # Data should be properly padded
