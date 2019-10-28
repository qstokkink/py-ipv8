import os
import random
import struct
import time

from twisted.internet import reactor
from twisted.internet.task import LoopingCall

from ipv8.community import Community
from ipv8.lazy_community import lazy_wrapper
from ipv8.messaging.lazy_payload import VariablePayload
from ipv8.peer import Peer


class PingPayload(VariablePayload):

    msg_id = 1
    names = ["nonce"]
    format_list = ['20s']


class PongPayload(VariablePayload):

    msg_id = 2
    names = ["nonce"]
    format_list = ['20s']


class RTTExperimentCommunity(Community):

    master_peer = Peer(("4c69624e61434c504b3a53204cdaae34bca3d5c3d6442ce5ec8cf2db04507ea038c7dde5503074bf4028a19e3a"
                        "4a5bccddc2b852a28af4522a41b5afe2d44424056b691e5d6e0c634a73").decode('hex'))

    def __init__(self, my_peer, endpoint, network, experiment_size, is_sybil=0, max_peers=-1, anonymize=False):
        super(RTTExperimentCommunity, self).__init__(my_peer, endpoint, network, max_peers, anonymize)
        self.sybil_map = {}
        self.is_sybil = is_sybil
        self.pong_delay = is_sybil * 0.05  # Delta = 0.05
        self.experiment_size = experiment_size
        self.max_iterations = 20

        self.RTTs = {}
        # List of lists of peers and measured pings
        self.measurements = []  # [(Peer1, Peer2, [nonces]), ]
        self.ping_window_size = 20

        self.add_message_handler(PingPayload.msg_id, self.on_ping)
        self.add_message_handler(PongPayload.msg_id, self.on_pong)

        if not self.is_sybil:
            with open(self.my_peer.mid.encode('hex') + '.map', 'w') as f:
                f.write('mid, is_sybil, estimated_sybil\n')
            with open(self.my_peer.mid.encode('hex') + '.sbl', 'w') as f:
                f.write('mid1, mid2, start_time, ping_time\n')
            self.iterations = 0
            self.register_task("update", LoopingCall(self.update)).start(0.5, False)

    def create_introduction_request(self, socket_address, extra_bytes=b''):
        extra_bytes = struct.pack('>?', self.is_sybil > 0)
        return super(RTTExperimentCommunity, self).create_introduction_request(socket_address, extra_bytes)

    def create_introduction_response(self, lan_socket_address, socket_address, identifier,
                                     introduction=None, extra_bytes=b''):
        extra_bytes = struct.pack('>?', self.is_sybil > 0)
        return super(RTTExperimentCommunity, self).create_introduction_response(lan_socket_address, socket_address,
                                                                             identifier, introduction, extra_bytes)

    def introduction_request_callback(self, peer, dist, payload):
        if payload.extra_bytes:
            is_sybil, = struct.unpack('>?', payload.extra_bytes)
            self.sybil_map[peer] = is_sybil
        if not peer.get_median_ping():
            self.send_ping(peer)

    def introduction_response_callback(self, peer, dist, payload):
        if payload.extra_bytes:
            is_sybil, = struct.unpack('>?', payload.extra_bytes)
            self.sybil_map[peer] = is_sybil
        if not peer.get_median_ping():
            self.send_ping(peer)

    def send_ping(self, peer):
        nonce = os.urandom(20)

        ping_cache = self.RTTs.get(peer, {})
        ping_cache[nonce] = [time.time(), -1]
        self.RTTs[peer] = ping_cache

        self.endpoint.send(peer.address, self.ezr_pack(PingPayload.msg_id, PingPayload(nonce)))

        return nonce

    def send_pong(self, peer, nonce):
        self.endpoint.send(peer.address, self.ezr_pack(PongPayload.msg_id, PingPayload(nonce)))

    @lazy_wrapper(PingPayload)
    def on_ping(self, peer, payload):
        self.register_anonymous_task('send_pong_later',
                                     reactor.callLater(self.pong_delay, self.send_pong, peer, payload.nonce))

    @lazy_wrapper(PongPayload)
    def on_pong(self, peer, payload):
        self.RTTs[peer][payload.nonce][1] = time.time()
        for key in self.RTTs:
            if key == peer:
                key.pings.append(self.RTTs[peer][payload.nonce][1] - self.RTTs[peer][payload.nonce][0])

    def estimate_sybils(self):
        sybil_map = {}
        previous_ping_time = -1
        for peer1, peer2, nonces in self.measurements:
            completed_measurements = []
            i = 0
            for nonce in nonces:
                start_time, end_time = self.RTTs[peer1 if i < self.ping_window_size else peer2][nonce]
                if end_time != -1:
                    completed_measurements.append((start_time, end_time - start_time))
                i += 1
            completed_measurements.sort()

            ping_time = completed_measurements[0][1]
            estimated_sybils = 0
            if previous_ping_time != -1:
                if ping_time > previous_ping_time:
                    estimated_sybils = 1
            previous_ping_time = completed_measurements[-1][1]

            sybil_map[peer1] = sybil_map.get(peer1, 0) + estimated_sybils
            sybil_map[peer2] = sybil_map.get(peer2, 0) + estimated_sybils
        return sybil_map

    def update(self):
        if (len(self.sybil_map) >= self.experiment_size
                and all(p.get_median_ping() is not None for p in self.sybil_map)
                and self.iterations < self.max_iterations):
            print "Running iteration", self.iterations
            if len(self.sybil_map) >= 2:
                peer1, peer2 = random.sample(self.sybil_map.keys(), 2)
                first_peer = peer1.get_median_ping() > peer2.get_median_ping()
                if not first_peer:
                    peer1, peer2 = peer2, peer1
                nonces = []
                # Send furthest first
                for _ in xrange(self.ping_window_size):
                    nonces.append(self.send_ping(peer1))
                # Send closest second
                for _ in xrange(self.ping_window_size):
                    nonces.append(self.send_ping(peer2))
                self.measurements.append((peer1, peer2, nonces))
            self.iterations += 1
        elif self.iterations == self.max_iterations:
            estimated_sybil_map = self.estimate_sybils()
            with open(self.my_peer.mid.encode('hex') + '.map', 'a') as f:
                for p in self.sybil_map:
                    f.write("%s, %d, %d\n" % (p.mid.encode('hex'), self.sybil_map[p],
                                              estimated_sybil_map.get(p, 0)))
            with open(self.my_peer.mid.encode('hex') + '.sbl', 'a') as f:
                for peer1, peer2, nonces in self.measurements:
                    i = 0
                    for nonce in nonces:
                        start_time, end_time = self.RTTs[peer1 if i < self.ping_window_size else peer2][nonce]
                        f.write('%s, %s, %f, %f\n' % (peer1.mid.encode('hex'),
                                                      peer2.mid.encode('hex'),
                                                      start_time,
                                                      (end_time - start_time) if end_time != -1 else -1))
                        i += 1
            print "Finished experiment!"
            self.iterations += 1
        else:
            if len(self.sybil_map) < self.experiment_size:
                print "Got:", len(self.sybil_map), "Waiting for:", self.experiment_size
            elif any(p.get_median_ping() is None for p in self.sybil_map):
                print "Missing pings:", [(p, p.get_median_ping()) for p in self.sybil_map]
