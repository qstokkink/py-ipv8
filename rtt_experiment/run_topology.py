import os
import random
import time
import sys

from twisted.internet import reactor

from .community2 import RTTExperimentCommunity, RTTExperimentIsolated
from .topology import create_topology
from ipv8_service import IPv8
from ipv8.configuration import get_default_configuration


WILD_PEERS = 55
SYBIL_PEERS = int(sys.argv[1], 10)
EXPERIMENT_SYBILS = [49]*20 #[49, 59, 69, 79, 89, 99] * 20


configuration = get_default_configuration()
configuration['walker_interval'] = 0.1
configuration['keys'] = [{
            'alias': "my peer",
            'generation': u'curve25519',
            'file': u"honestec.pem"
        }]
configuration['overlays'] = [{
        'class': 'RTTExperimentIsolated',
        'key': "my peer",
        'walkers': [
            {
                'strategy': "RandomWalk",
                'peers': SYBIL_PEERS,
                'init': {
                    'timeout': 60.0,
                    'window_size': SYBIL_PEERS,
                    'reset_chance': 10
                }
            }
        ],
        'initialize': {
            'experiment_size': SYBIL_PEERS,
            'is_sybil': 1
        },
        'on_start': []
    },
    {
        'class': 'RTTExperimentCommunity',
        'key': "my peer",
        'walkers': [
            {
                'strategy': "RandomWalk",
                'peers': WILD_PEERS,
                'init': {
                    'timeout': 60.0,
                    'window_size': WILD_PEERS,
                    'reset_chance': 10
                }
            }
        ],
        'initialize': {
            'max_peers': WILD_PEERS,
            'experiment_size': WILD_PEERS,
            'is_sybil': 1
        },
        'on_start': [
            ('resolve_dns_bootstrap_addresses',)
        ]
    }
]
configuration['logger'] = { 'level': "INFO" }

ipv8 = IPv8(configuration, extra_communities={
    'RTTExperimentIsolated': RTTExperimentIsolated,
    'RTTExperimentCommunity': RTTExperimentCommunity
})


def start_experiment(honest_community, sybil_community, sybil_count):
    honest_count = 100 - sybil_count

    poolA = set(random.sample(honest_community.get_peers(), honest_count) if honest_count else [])
    poolB = set(random.sample(sybil_community.get_peers(), sybil_count) if sybil_count else [])

    poolAB = list(poolA | poolB)
    poolA = list(poolA)
    poolB = list(poolB)

    bootstrap_func = lambda: random.choice(poolAB)
    walk_func = lambda from_peer: random.choice(poolB if from_peer in poolB else poolAB)
    ping_func = lambda peer: (sybil_community if peer in poolB else honest_community).send_ping(peer, True)
    get_ping_func = lambda peer, nonce: (sybil_community.RTTs[peer][nonce]
                                         if sybil_community.RTTs.get(peer, {}).get(nonce, None) is not None else
                                         honest_community.RTTs[peer][nonce])

    start_time = time.time()
    psot = create_topology(bootstrap_func, walk_func, ping_func, get_ping_func, update_rate=0.5, experiment_time=5*60.0)
    print "Experiment concluded, processing!"

    outfile = str(sybil_count) + ".msr"
    if not os.path.exists(outfile):
        with open(outfile, 'w') as f:
            f.write("time,count\n")

    first_honest_time = None
    honest_progression_x = []
    honest_progression_y = []
    print "Start time:", start_time
    with open(outfile, 'a') as f:
        for timestamp, progression in psot:
            honest_peers = len([p for p in progression if p in poolA])
            if first_honest_time is None and honest_peers > 0:
                first_honest_time = timestamp
                print "Time of first honest peer:", timestamp
            honest_progression_x.append(timestamp)
            honest_progression_y.append(honest_peers)
            f.write("%f,%d\n" % (timestamp-start_time, honest_peers))
    print "Honest progression t:", honest_progression_x
    print "Honest progression y:", honest_progression_y



def start_experiments(honest_community, sybil_community):
    for sybil_count in EXPERIMENT_SYBILS:
        start_experiment(honest_community, sybil_community, sybil_count)
    reactor.callFromThread(reactor.stop)


def check_experiment_start(ipv8):
    ready_overlays = 0
    for overlay in ipv8.overlays:
        ready_peers = len([p for p in overlay.get_peers() if p.get_median_ping() is not None])
        print overlay, ready_peers
        if isinstance(overlay, RTTExperimentIsolated):
            ready_overlays += 1 if ready_peers >= SYBIL_PEERS else 0
        else:
            ready_overlays += 1 if ready_peers >= WILD_PEERS else 0
        for peer in overlay.get_peers():
            if peer.get_median_ping() is None:
                overlay.send_ping(peer)
                continue
    if ready_overlays == 2:
        sybil_community = [overlay for overlay in ipv8.overlays if isinstance(overlay, RTTExperimentIsolated)][0]
        honest_community = [overlay for overlay in ipv8.overlays if overlay != sybil_community][0]
        reactor.callInThread(start_experiments, honest_community, sybil_community)
    else:
        reactor.callLater(5.0, check_experiment_start, ipv8)


reactor.callWhenRunning(check_experiment_start, ipv8)

reactor.run()
