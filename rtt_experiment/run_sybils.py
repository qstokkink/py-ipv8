import sys

from twisted.internet import reactor

from .community import RTTExperimentCommunity
from ipv8_service import IPv8
from ipv8.configuration import get_default_configuration

sybil_count = int(sys.argv[2], 10)

for i in xrange(1, sybil_count+1):
    configuration = get_default_configuration()
    configuration['keys'] = [{
                'alias': "my peer",
                'generation': u'curve25519',
                'file': u"sybilec%d.pem" % i
            }]
    configuration['overlays'] = [{
        'class': 'RTTExperimentCommunity',
        'key': "my peer",
        'walkers': [{
                        'strategy': "RandomWalk",
                        'peers': -1,
                        'init': {
                            'timeout': 3.0
                        }
                    }],
        'initialize': {'experiment_size': int(sys.argv[1], 10),
                       'is_sybil': i},
        'on_start': []
    }]

    IPv8(configuration, extra_communities={'RTTExperimentCommunity': RTTExperimentCommunity})

reactor.run()