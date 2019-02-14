from threading import Thread
from time import sleep, time

from twisted.internet import reactor

from .database import TraceDB
from ..lazy_community import lazy_wrapper_unsigned
from ..messaging.interfaces.udp.endpoint import UDPEndpoint
from ..messaging.payload_headers import GlobalTimeDistributionPayload
from ..peerdiscovery.discovery import DiscoveryStrategy
from ..peerdiscovery.community import DiscoveryCommunity
from ..peerdiscovery.payload import PongPayload
from ..requestcache import NumberCache, RequestCache
from .subprocess import Subprocess


REQUIRED_SACRIFICES = 20
MAX_ATTACK_LEVEL = 64


def traceroute_to_tuplelist(raw_traceroute):
    lines = raw_traceroute.split('\n')
    out = []
    for line in lines:
        words = line.split(' ')
        if words and words[-1] == "ms":
            try:
                time = float(words[-2])/1000.0
                ip = words[-4][1:-1]
                out.append((ip, time))
            except:
                import traceback
                traceback.print_exc()
    return out


def mtr_to_tuplelist(raw_mtr):
    lines = raw_mtr.split('\n')
    out = []
    for line in lines[1:]:
        words = line.split(' ')
        words = [word for word in words if word.strip()]
        if words and words[-1] != "Last":
            try:
                time = float(words[-1]) / 1000.0
                ip = words[-2]
                if ip != '???':
                    out.append((ip, time))
            except:
                import traceback
                traceback.print_exc()
    return out


class SybilStrategy(DiscoveryStrategy):

    def __init__(self, overlay):
        super(SybilStrategy, self).__init__(overlay)

        self.victim_history = []
        self.victim = None
        self.attack_level = 1

    def take_step(self):
        with self.walk_lock:
            if not self.victim and self.overlay.attack_complete:
                # FIND A POOR HELPLESS USER!
                self.overlay.get_new_introduction()
                known = self.overlay.get_walkable_addresses()
                for address in known:
                    self.overlay.walk_to(address)
                # TRY TO APPEASE THE DARK GODS WITH A SACRIFICE!
                for peer in self.overlay.get_peers():
                    if (peer.public_key == self.overlay.my_peer.public_key or
                            peer.address in self.overlay.network.blacklist):
                        continue
                    # CAN'T SACRIFICE THE SAME VICTIM TWICE, OBVIOUSLY!
                    if peer not in self.victim_history:
                        self.victim = peer
                        self.victim_history.append(peer)
                        break
                    else:
                        self.overlay.network.remove_peer(peer)
            elif self.attack_level <= MAX_ATTACK_LEVEL and self.overlay.attack_complete:
                # ATTACK!
                self.overlay.launch_attack(self.victim, self.attack_level)
                self.attack_level *= 4
            elif self.attack_level > MAX_ATTACK_LEVEL and self.overlay.attack_complete:
                # NEXT VICTIM!
                self.victim = None
                self.attack_level = 1
                if len(self.victim_history) >= REQUIRED_SACRIFICES:
                    print "Required sacrifices have been met. Exiting."
                    self.overlay.database.close()
                    reactor.callFromThread(reactor.stop)


class AttackCache(NumberCache):

    def __init__(self, request_cache, identifier, address):
        super(AttackCache, self).__init__(request_cache, u"attack", identifier)
        self.start_time = time()
        self.address = address

    def on_timeout(self):
        pass # Ignore failed attacks


class SybilCommunity(DiscoveryCommunity):

    def __init__(self, my_peer, endpoint, network, max_peers=-1):
        super(SybilCommunity, self).__init__(my_peer, endpoint, network, max_peers)
        self._use_main_thread = False

        self.endpoints = [UDPEndpoint(21000 + i*2) for i in xrange(256)]
        for endpoint in self.endpoints:
            endpoint.add_listener(self)
            endpoint.open()

        self.request_cache = RequestCache()
        self.attack_complete = True

        self.database = TraceDB(u"sqlite/tracedb.db")
        self.database.open()

    def get_available_strategies(self):
        return {"SybilStrategy": SybilStrategy}

    def launch_attack(self, victim, attack_level):
        print "Launching attack on", victim, "with", attack_level, "identities!"
        self.attack_complete = False
        victim_address = victim.address
        open_traceroutes = []
        open_mtrs = []
        for i in xrange(attack_level):
            endpoint = self.endpoints[i]

            packet = self.create_ping()
            self.request_cache.add(AttackCache(self.request_cache, self.global_time, victim.address))
            endpoint.send(victim.address, packet)

            #command = "traceroute -m 15 -q 1 -p %d -T %s" % (victim.address[1], victim.address[0])
            #command = "traceroute -m 15 -q 1 -I %s %d" % (victim.address[0], victim.address[1])
            process = Subprocess()
            reactor.spawnProcess(process, "traceroute", ["traceroute", "-m", "15", "-q", "1", "-I", victim.address[0], str(victim.address[1])])
            #process = subprocess.Popen([command], stdout=subprocess.PIPE, shell=True)
            open_traceroutes.append(process)

            #command = "mtr -o \"N\" -l -n -c 1 --report %s" % victim.address[0]
            #process = subprocess.Popen([command], stdout=subprocess.PIPE, shell=True)
            process = Subprocess()
            reactor.spawnProcess(process, "mtr", ["mtr", "-o", "N", "-l", "-n", "-c", "1", "--report",  victim.address[0]])
            open_mtrs.append(process)

        def wait_for_results():
            print "Waiting for traceroutes to complete."
            for process in open_traceroutes:
                while not process.done:
                    sleep(0.05)
                self.database.insert_measurement(victim_address, attack_level, "traceroute", traceroute_to_tuplelist(process.data))
            print "Waiting for mtrs to complete."
            for process in open_mtrs:
                while not process.done:
                    sleep(0.05)
                self.database.insert_measurement(victim_address, attack_level, "mtr", mtr_to_tuplelist(process.data))
            self.attack_complete = True
        t = Thread(target=wait_for_results)
        t.start()


    @lazy_wrapper_unsigned(GlobalTimeDistributionPayload, PongPayload)
    def on_pong(self, source_address, dist, payload):
        try:
            cache = self.request_cache.pop(u"attack", payload.identifier)
            elapsed_time = time() - cache.start_time
            self.database.insert_measurement(cache.address, 1, "ping", [(cache.address[0], elapsed_time)])
        except KeyError:
            pass
