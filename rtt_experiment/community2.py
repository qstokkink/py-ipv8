import itertools
import math
import random
import time

from twisted.internet import reactor
from twisted.internet.task import LoopingCall

import ipv8.community
ipv8.community.BOOTSTRAP_TIMEOUT = 10.0

from ipv8.lazy_community import lazy_wrapper_unsigned
from ipv8.peerdiscovery.community import DiscoveryCommunity
from ipv8.messaging.payload_headers import GlobalTimeDistributionPayload
from ipv8.peer import Peer
from ipv8.peerdiscovery.community import PingRequestCache
from ipv8.peerdiscovery.payload import PingPayload, PongPayload


class PingRequestTerminateCache(PingRequestCache):

    def __init__(self, request_cache, identifier, peer, start_time, network):
        super(PingRequestTerminateCache, self).__init__(request_cache, identifier, peer, start_time)
        self.network = network

    def on_timeout(self):
        self.network.remove_peer(self.peer)


class RTTExperimentCommunity(DiscoveryCommunity):

    def __init__(self, my_peer, endpoint, network, experiment_size,
                 ipv8s=[], is_sybil=0, max_peers=-1, anonymize=False):
        super(RTTExperimentCommunity, self).__init__(my_peer, endpoint, network, max_peers, anonymize)
        self.ipv8s = ipv8s
        self.is_sybil = is_sybil
        self.pong_delay = max(0, is_sybil - 1) * 0.05  # Delta = 0.05
        self.experiment_size = experiment_size
        self.RTTs = {}
        self.measurements = []  # [(Peer1, Peer2, [nonces]), ]
        self.ping_window_size = 20
        self.victim_set = {}
        self.outstanding_checks = None
        self.completed = False
        self.measuring = False
        self.reverse_nonce_map = {}
        self.expected_measurements = math.factorial(experiment_size) / math.factorial(2) / math.factorial(experiment_size - 2)

        if not self.is_sybil:
            with open(self.my_peer.mid.encode('hex') + '.map', 'w') as f:
                f.write('address, ping, honest, sybil\n')
            with open(self.my_peer.mid.encode('hex') + '.sbl', 'w') as f:
                f.write('address1, address2, start_time, ping_time\n')
            self.register_task("update", LoopingCall(self.update)).start(5.0, False)

    def walk_to(self, address):
        if not self.measuring:
            super(RTTExperimentCommunity, self).walk_to(address)

    def on_similarity_request(self, source_address, packet):
        pass

    def on_similarity_response(self, source_address, packet):
        pass

    def introduction_response_callback(self, peer, dist, payload):
        if not self.measuring and payload.wan_introduction_address[0] != '0.0.0.0':
            self.walk_to(payload.wan_introduction_address)
            self.send_ping(peer)

    def introduction_request_callback(self, peer, dist, payload):
        if not self.measuring:
            self.walk_to(payload.wan_introduction_address)

    def send_ping(self, peer, no_cache=False):
        global_time = self.claim_global_time()
        nonce = global_time % 65536

        payload = PingPayload(nonce).to_pack_list()
        dist = GlobalTimeDistributionPayload(global_time).to_pack_list()

        packet = self._ez_pack(self._prefix, 3, [dist, payload], False)
        if no_cache:
            self.reverse_nonce_map[nonce] = peer
        else:
            self.request_cache.add(PingRequestTerminateCache(self.request_cache, nonce, peer, time.time(), self.network))

        ping_cache = self.RTTs.get(peer, {})
        ping_cache[nonce] = [time.time(), -1]
        self.RTTs[peer] = ping_cache

        self.endpoint.send(peer.address, packet)

        return nonce

    @lazy_wrapper_unsigned(GlobalTimeDistributionPayload, PingPayload)
    def on_ping(self, source_address, dist, payload):
        packet = self.create_pong(payload.identifier)
        if self.pong_delay:
            self.register_anonymous_task('send_pong_later',
                                         reactor.callLater(self.pong_delay, self.endpoint.send,
                                                           source_address, packet))
        else:
            self.endpoint.send(source_address, packet)

    @lazy_wrapper_unsigned(GlobalTimeDistributionPayload, PongPayload)
    def on_pong(self, source_address, dist, payload):
        rcv_time = time.time()
        peer = self.reverse_nonce_map.pop(payload.identifier, None)
        if not peer:
            try:
                cache = self.request_cache.pop(u"discoverypingcache", payload.identifier)
            except KeyError:
                return
            cache.finish()
            peer = cache.peer
        self.RTTs[peer][payload.identifier][1] = rcv_time

    def estimate_sybils(self):
        sybil_map_clsf = {}
        honest_map_clsf = {}
        for peer1, peer2, nonces in self.measurements:
            # 1. Reconstruct measurements
            ping_times = []
            i = 0
            for nonce in nonces:
                if self.RTTs.get(peer1, {}).get(nonce, None) is not None:
                    start_time, end_time = self.RTTs[peer1][nonce]
                else:
                    start_time, end_time = self.RTTs[peer2][nonce]
                if end_time != -1:
                    ping_times.append((start_time, end_time - start_time))
                i += 1
            ping_times.sort()
            # 4. Classifier
            dip = len(ping_times)
            if dip == 0 or float(ping_times[dip-1][0] - ping_times[0][0]) == 0:
                continue
            coef = float(ping_times[dip-1][1] - ping_times[0][1])/float(ping_times[dip-1][0] - ping_times[0][0])
            mse = 1/float(dip) * sum(math.pow(coef * (ping_times[x][0] - ping_times[0][0]) - ping_times[x][1], 2)
                                     for x in xrange(dip))
            is_sybil_clsf2 = mse < 0.01
            if is_sybil_clsf2:
                sybil_map_clsf[peer1] = sybil_map_clsf.get(peer1, 0) + 1
                sybil_map_clsf[peer2] = sybil_map_clsf.get(peer2, 0) + 1
            else:
                honest_map_clsf[peer1] = honest_map_clsf.get(peer1, 0) + 1
                honest_map_clsf[peer2] = honest_map_clsf.get(peer2, 0) + 1
        return honest_map_clsf, sybil_map_clsf

    def update(self):
        if (len(self.victim_set) >= self.experiment_size
                and all(p.get_median_ping() is not None for p in self.victim_set)
                and not self.completed):
            if not self.measuring:
                print "Starting measurement, nuking IPv8 walkers"
                self.cancel_pending_task("update")
                self.register_task("update", LoopingCall(self.update)).start(0.5, False)
                for ipv8 in self.ipv8s:
                    ipv8.state_machine_lc.stop()
                self.measuring = True
                return
            try:
                if len(self.victim_set) >= 2:
                    if self.outstanding_checks is None:
                        self.outstanding_checks = itertools.combinations(self.victim_set, 2)
                    if len(self.measurements) % 50 == 0:
                        print len(self.measurements), '/', self.expected_measurements
                    peer1, peer2 = self.outstanding_checks.next()
                    first_peer = peer1.get_median_ping() > peer2.get_median_ping()
                    if not first_peer:
                        peer1, peer2 = peer2, peer1
                    nonces = []
                    ping_window_repeats = max(1, int(math.ceil(peer1.get_median_ping()/0.2)))
                    for _ in xrange(ping_window_repeats):
                        # Send furthest first
                        for _ in xrange(self.ping_window_size):
                            nonces.append(self.send_ping(peer1, True))
                        # Send closest second
                        for _ in xrange(self.ping_window_size):
                            self.send_ping(peer2, True)  # We don't store this in the nonce list.
                    self.measurements.append((peer1, peer2, nonces))
                else:
                    raise StopIteration()
            except StopIteration:
                self.completed = True
                print "Waiting for final measurements"
                time.sleep(5.0)
                honest_map_clsf, sybil_map_clsf = self.estimate_sybils()
                with open(self.my_peer.mid.encode('hex') + '.map', 'a') as f:
                    for p in self.victim_set:
                        f.write("%s, %f, %d, %d\n" % (p.address[0] + ':' + str(p.address[1]),
                                                      p.get_median_ping(),
                                                      honest_map_clsf.get(p, 0), sybil_map_clsf.get(p, 0)))
                with open(self.my_peer.mid.encode('hex') + '.sbl', 'a') as f:
                    for peer1, peer2, nonces in self.measurements:
                        i = 0
                        for nonce in nonces:
                            if self.RTTs.get(peer1, {}).get(nonce, None) is not None:
                                start_time, end_time = self.RTTs[peer1][nonce]
                            else:
                                start_time, end_time = self.RTTs[peer2][nonce]
                            f.write('%s, %s, %f, %f\n' % (peer1.address[0] + ':' + str(peer1.address[1]),
                                                          peer2.address[0] + ':' + str(peer2.address[1]),
                                                          start_time,
                                                          (end_time - start_time) if end_time != -1 else -1))
                            i += 1
                print "Finished experiment!"
                reactor.callFromThread(reactor.stop)
        else:
            if len(self.victim_set) < self.experiment_size:
                print "Got:", len(self.get_peers()), "Waiting for:", self.experiment_size
                if len(self.get_peers()) >= self.experiment_size:
                    self.unique_addresses = {p: p for p in self.get_peers()} #{p.address[0]: p for p in self.get_peers()}
                    self.victim_set = set(random.sample(self.unique_addresses.values(),
                                                        min(len(self.unique_addresses), self.experiment_size)))
                    print "Unique:", len(self.unique_addresses)
                self.bootstrap()
                if self.network._all_addresses:
                    for address in random.sample(self.network._all_addresses,
                                                 min(20, len(self.network._all_addresses))):
                        self.walk_to(address)
            elif any(p.get_median_ping() is None for p in self.victim_set):
                missing_ping_set = [p for p in self.victim_set if p.get_median_ping() is None]
                print "Missing pings for %d peers!" % len(missing_ping_set)
                if missing_ping_set:
                    for p in random.sample(missing_ping_set, min(20, len(missing_ping_set))):
                        if not p.get_median_ping():
                            self.send_ping(p)
                self.victim_set = set(p for p in self.victim_set if p.get_median_ping())


class RTTExperimentIsolated(RTTExperimentCommunity):

    master_peer = Peer(("4c69624e61434c504b3a53204cdaae34bca3d5c3d6442ce5ec8cf2db04507ea038c7dde5503074bf4028a19e3a"
                        "4a5bccddc2b852a28af4522a41b5afe2d44424056b691e5d6e0c634a73").decode('hex'))
