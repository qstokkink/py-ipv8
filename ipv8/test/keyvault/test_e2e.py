from __future__ import absolute_import

from twisted.internet.defer import inlineCallbacks

from ..base import TestBase
from ...keyvault.crypto import default_eccrypto
from ...keyvault.e2e_overlay import E2EListener
from ..mocking.endpoint import AutoMockEndpoint, MockEndpointListener
from ...peer import Peer
from ...peerdiscovery.network import Network


class TestEncryption(TestBase):

    @inlineCallbacks
    def setUp(self):
        yield super(TestEncryption, self).setUp()

        for _ in xrange(2):
            ep = AutoMockEndpoint()
            ep.open()
            peer = Peer(default_eccrypto.generate_key(u"curve25519"))
            peer.address = ep.wan_address
            self.nodes.append(E2EListener(peer, ep, Network()))
        self.nodes[0].network.add_verified_peer(self.nodes[1].my_peer)
        self.nodes[1].network.add_verified_peer(self.nodes[0].my_peer)

    @inlineCallbacks
    def tearDown(self):
        self.nodes = []
        yield super(TestEncryption, self).tearDown()

    @inlineCallbacks
    def test_something(self):
        message = b'Hello!'

        mock_listener = MockEndpointListener(self.nodes[1].endpoint)
        self.nodes[0].send(self.nodes[1].my_peer, message)

        yield self.deliver_messages()

        self.assertIn(message, [value for (_, value) in mock_listener.received_packets])