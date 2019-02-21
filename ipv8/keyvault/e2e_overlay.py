from __future__ import absolute_import

from logging import warning
import os

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x448 import X448PrivateKey, X448PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from twisted.internet.defer import Deferred

from ..keyvault.public.libnaclkey import LibNaCLPK
from ..messaging.interfaces.endpoint import EndpointListener


class E2EListener(EndpointListener):

    def __init__(self, my_peer, endpoint, network, main_thread=True):
        super(E2EListener, self).__init__(endpoint, main_thread)

        self.my_peer = my_peer
        self.network = network

        self.keys = {} # Ephemeral key store

        endpoint.add_listener(self)

    def _raw_send(self, peer, data):
        self.endpoint.send(peer.address, b'\x01' + self.my_peer.key.encrypt(data, peer.public_key))

    def _initialize_connection(self, peer):
        private_key = X448PrivateKey.generate()
        self.keys[peer.public_key] = (Deferred(), private_key, None)
        self._raw_send(peer, private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        return self.keys[peer.public_key][0]

    def _finish_connection(self, peer, data):
        existing = self.keys.get(peer.public_key)
        if existing:
            private_key = existing[1]
            decrypted_data = self.my_peer.key.decrypt(data, peer.public_key)
            iv = decrypted_data[:16]
            decrypted_pk = X448PublicKey.from_public_bytes(decrypted_data[16:])
            deferred = existing[0]
        else:
            private_key = X448PrivateKey.generate()
            decrypted_pk = X448PublicKey.from_public_bytes(self.my_peer.key.decrypt(data, peer.public_key))
            iv = os.urandom(16)
            deferred = Deferred()
        shared_key = private_key.exchange(decrypted_pk)
        derived_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b'handshake data',
                           backend=default_backend()).derive(shared_key)
        cipher = Cipher(algorithms.AES(derived_key), modes.CBC(iv), backend=default_backend())
        self.keys[peer.public_key] = (deferred, private_key, cipher)
        deferred.callback(None)
        if not existing:
            self._raw_send(peer, iv + private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    def send(self, peer, data):
        if peer.public_key in self.keys and self.keys[peer.public_key][2]:
            encryptor = self.keys[peer.public_key][2].encryptor()
            padder = padding.PKCS7(128).padder()
            padded = padder.update(data) + padder.finalize()
            self.endpoint.send(peer.address, b'\x01' + encryptor.update(padded) + encryptor.finalize())
        elif peer.public_key not in self.keys:
            self._initialize_connection(peer).addCallback(lambda _: self.send(peer, data))

    def on_packet(self, packet):
        source_address, data = packet
        if not data or data[0:1] != b'\x01':
            return
        data = data[1:]
        peer = self.network.get_verified_by_address(source_address)
        if peer and isinstance(peer.public_key, LibNaCLPK):
            if peer.public_key in self.keys and self.keys[peer.public_key][2]:
                try:
                    decryptor = self.keys[peer.public_key][2].decryptor()
                    decoded = decryptor.update(data) + decryptor.finalize()
                    unpadder = padding.PKCS7(128).unpadder()
                    unpadded = unpadder.update(decoded) + unpadder.finalize()
                    self.endpoint.notify_listeners((peer.address, unpadded))
                except:
                    warning("Decryption for message from %s failed, resetting connection!", str(source_address))
                    self.keys.pop(peer.public_key)
                    self._initialize_connection(peer)
                return

            self._finish_connection(peer, data)
        else:
            warning("Dropping message from %s, unsupported peer!", str(source_address))
