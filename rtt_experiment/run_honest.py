import sys

from twisted.internet import reactor

from .community2 import RTTExperimentIsolated
from ipv8_service import IPv8
from ipv8.configuration import get_default_configuration

ipv8_instances = []
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
        'ipv8s': ipv8_instances
    },
    'on_start': []
}]
configuration['logger'] = { 'level': "INFO" }

ipv8_instances.append(IPv8(configuration, extra_communities={'RTTExperimentCommunity': RTTExperimentIsolated}))

reactor.run()
