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


def check_experiment_start(ipv8):
    ready_overlays = 0
    for overlay in ipv8.overlays:
        print overlay, len(overlay.get_peers())
        if isinstance(overlay, RTTExperimentIsolated):
            ready_overlays += 1 if len(overlay.get_peers()) >= SYBIL_PEERS else 0
        else:
            ready_overlays += 1 if len(overlay.get_peers()) >= WILD_PEERS else 0
    if ready_overlays == 2:
        print "Done!"  # TODO
    else:
        reactor.callLater(5.0, check_experiment_start, ipv8)


reactor.callWhenRunning(check_experiment_start, ipv8)

reactor.run()
