import random
import sys

from twisted.internet import reactor

from .community2 import RTTExperimentIsolated
from ipv8_service import IPv8
from ipv8.configuration import get_default_configuration


WILD_PEERS = 50
SYBIL_PEERS = int(sys.argv[1], 10)


configuration = get_default_configuration()
configuration['walker_interval'] = 0.1
configuration['keys'] = [{
            'alias': "my peer",
            'generation': u'curve25519',
            'file': u"honestec.pem"
        }]
configuration['overlays'] = [{
        'class': 'RTTExperimentCommunity',
        'key': "my peer",
        'walkers': [],
        'initialize': {
            'experiment_size': SYBIL_PEERS,
            'is_sybil': 1
        },
        'on_start': []
    },
    {
        'class': 'DiscoveryCommunity',
        'key': "my peer",
        'walkers': [
            {
                'strategy': "RandomWalk",
                'peers': WILD_PEERS,
                'init': {
                    'timeout': 60.0,
                    'window_size': int(sys.argv[1], 10),
                    'reset_chance': 10
                }
            }
        ],
        'initialize': {
            'max_peers': WILD_PEERS
        },
        'on_start': [
            ('resolve_dns_bootstrap_addresses',)
        ]
    }
]
configuration['logger'] = { 'level': "INFO" }

ipv8 = IPv8(configuration, extra_communities={'RTTExperimentCommunity': RTTExperimentIsolated})


def start_experiment(honest_community, sybil_community, sybil_count):
    honest_count = 100 - sybil_count # 100 peers total

    poolA = set(random.sample(honest_community.get_peers(), honest_count))
    poolB = set(random.sample(sybil_community.get_peers(), sybil_count))
    poolAB = poolA | poolB

    bootstrap_func = lambda: random.choice(poolAB)
    walk_func = lambda from_peer: random.choice(poolB if from_peer in poolB else poolAB)

    # TODO:
    #create_topology(bootstrap_func, walk_func, update_rate=0.5, experiment_time=60.0)


def start_experiments(honest_community, sybil_community):
    for sybil_count in [49, 59, 69, 79, 89, 99]:
        start_experiment(honest_community, sybil_community, sybil_count)


def check_experiment_start(ipv8):
    ready_overlays = 0
    for overlay in ipv8.overlays:
        print overlay, len(overlay.get_peers())
        if isinstance(overlay, RTTExperimentIsolated):
            ready_overlays += 1 if len(overlay.get_peers()) >= SYBIL_PEERS else 0
        else:
            ready_overlays += 1 if len(overlay.get_peers()) >= WILD_PEERS else 0
    if ready_overlays == 2:
        sybil_community = [overlay for overlay in ipv8.overlays if isinstance(overlay, RTTExperimentIsolated)][0]
        honest_community = [overlay for overlay in ipv8.overlays if overlay != sybil_community][0]
        reactor.callInThread(start_experiments, honest_community, sybil_community)
    else:
        reactor.callLater(5.0, check_experiment_start, ipv8)


reactor.callWhenRunning(check_experiment_start, ipv8)

reactor.run()
