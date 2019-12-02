import sys

from twisted.internet import reactor

from .community2 import RTTExperimentIsolated
from ipv8_service import IPv8
from ipv8.configuration import get_default_configuration


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
            'experiment_size': int(sys.argv[1], 10),
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
                'peers': 50,
                'init': {
                    'timeout': 60.0
                }
            }
        ],
        'initialize': {
            'max_peers': 50
        },
        'on_start': [
            ('resolve_dns_bootstrap_addresses',)
        ]
    }
]
configuration['logger'] = { 'level': "INFO" }

IPv8(configuration, extra_communities={'RTTExperimentCommunity': RTTExperimentIsolated})

reactor.run()
