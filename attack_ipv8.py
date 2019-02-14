from twisted.internet import reactor

from ipv8_service import IPv8
from ipv8.sybilattack.community import SybilCommunity
from ipv8.configuration import get_default_configuration

configuration = get_default_configuration()
configuration['logger']['level'] = 'INFO'
configuration['keys'] = [{
                'alias': "my peer",
                'generation': u"medium",
                'file': u"attack_peer_ec.pem"
            }]
configuration['overlays'] = [{
    'class': 'SybilCommunity',
    'key': "my peer",
    'walkers': [{
                    'strategy': "SybilStrategy",
                    'peers': -1,
                    'init': {}
                }],
    'initialize': {},
    'on_start': []
}]
IPv8(configuration, extra_communities={'SybilCommunity': SybilCommunity})

reactor.run()
