import hashlib
import os
import time
from asyncio import Future, gather
from binascii import hexlify, unhexlify
from collections import defaultdict, deque
from itertools import zip_longest

from . import DHTError
from .payload import (FindRequestPayload, FindResponsePayload, PingRequestPayload, PingResponsePayload,
                      SignedStrPayload, StoreRequestPayload, StoreResponsePayload, StrPayload)
from .routing import Node, RoutingTable, calc_node_id, distance
from .storage import Storage
from ..community import Community
from ..lazy_community import lazy_wrapper, lazy_wrapper_wd
from ..messaging.payload_headers import BinMemberAuthenticationPayload, GlobalTimeDistributionPayload
from ..peer import Peer
from ..peerdiscovery.churn import PingChurn
from ..requestcache import RandomNumberCache, RequestCache
from ..taskmanager import task
from ..util import cast_to_bin

PING_INTERVAL = 25

# Maximum number of seconds a token can remain valid
TOKEN_EXPIRATION_TIME = 600

DHT_ENTRY_STR = 0
DHT_ENTRY_STR_SIGNED = 1

MAX_ENTRY_SIZE = 170
MAX_ENTRY_AGE = 86400

MAX_FIND_WALKS = 8
MAX_FIND_STEPS = 4

MAX_VALUES_IN_STORE = 8
MAX_VALUES_IN_FIND = 8
MAX_NODES_IN_FIND = 8

# Target number of nodes at which a key-value pair should be stored
TARGET_NODES = 8

MSG_PING = 7
MSG_PONG = 8
MSG_STORE_REQUEST = 9
MSG_STORE_RESPONSE = 10
MSG_FIND_REQUEST = 11
MSG_FIND_RESPONSE = 12


async def gather_without_errors(*futures):
    results = await gather(*futures, return_exceptions=True)
    return [r for r in results if not isinstance(r, Exception)]


class Request(RandomNumberCache):
    """
    This request cache keeps track of all outstanding requests within the DHTCommunity.
    """
    def __init__(self, community, msg_type, node, params=None, consume_errors=False):
        super(Request, self).__init__(community.request_cache, msg_type)
        self.msg_type = msg_type
        self.node = node
        self.params = params
        self.future = Future()
        self.start_time = time.time()
        self.consume_errors = consume_errors

    @property
    def timeout_delay(self):
        return 5.0

    def on_timeout(self):
        if not self.future.done():
            self._logger.debug('Timeout for %s to %s', self.msg_type, self.node)
            self.node.failed += 1
            if not self.consume_errors:
                self.future.set_exception(DHTError('Timeout for {} to {}'.format(self.msg_type, self.node)))
            else:
                self.future.set_result(None)

    def on_complete(self):
        self.node.last_response = time.time()
        self.node.failed = 0
        self.node.rtt = time.time() - self.start_time


class DHTCommunity(Community):
    """
    Community for storing/finding key-value pairs.
    """
    master_peer = Peer(unhexlify('4c69624e61434c504b3a4c99f04cef9ba4ca645401cd51b8ef634e63e2ad0d3209eca958ce0293d7cf668'
                                 '2059469c1a253e66191bd3b96a082a8e11cc35962b9b6f8434e21518a0344af'))

    def __init__(self, *args, **kwargs):
        super(DHTCommunity, self).__init__(*args, **kwargs)
        self.routing_table = RoutingTable(self.my_node_id)
        self.storage = Storage()
        self.request_cache = RequestCache()
        self.tokens = {}
        self.token_secrets = deque(maxlen=2)
        self.register_task('value_maintenance', self.value_maintenance, interval=3600)
        self.register_task('token_maintenance', self.token_maintenance, interval=300, delay=0)

        # Register messages
        self.decode_map.update({
            chr(MSG_PING): self.on_ping_request,
            chr(MSG_PONG): self.on_ping_response,
            chr(MSG_STORE_REQUEST): self.on_store_request,
            chr(MSG_STORE_RESPONSE): self.on_store_response,
            chr(MSG_FIND_REQUEST): self.on_find_request,
            chr(MSG_FIND_RESPONSE): self.on_find_response,
        })

        self.logger.info('DHT community initialized (peer mid %s)', hexlify(self.my_peer.mid))

    def get_available_strategies(self):
        return {'PingChurn': PingChurn}

    async def unload(self):
        await self.request_cache.shutdown()
        await super(DHTCommunity, self).unload()

    @property
    def my_node_id(self):
        return calc_node_id(self.my_peer.address[0], self.my_peer.mid)

    def send_message(self, address, message_id, payload_cls, payload_args):
        global_time = self.claim_global_time()
        auth = BinMemberAuthenticationPayload(self.my_peer.public_key.key_to_bin()).to_pack_list()
        payload = payload_cls(*payload_args).to_pack_list()
        dist = GlobalTimeDistributionPayload(global_time).to_pack_list()
        packet = self._ez_pack(self._prefix, message_id, [auth, dist, payload])
        return self.endpoint.send(address, packet)

    def get_requesting_node(self, peer):
        node = Node(peer.key, peer.address)

        if self.routing_table.has(node.id) and self.routing_table.get(node.id).blocked:
            self.logger.debug('Too many queries\'s, dropping packet')
            return

        node = self.routing_table.add(node) or node
        node.last_queries.append(time.time())
        return node

    def introduction_request_callback(self, peer, dist, payload):
        self.on_node_discovered(peer.public_key.key_to_bin(), peer.address)

    def introduction_response_callback(self, peer, dist, payload):
        self.on_node_discovered(peer.public_key.key_to_bin(), peer.address)

    def on_node_discovered(self, public_key_bin, source_address):
        # Filter out trackers
        if source_address not in self.network.blacklist:
            node = Node(public_key_bin, source_address)
            existed = self.routing_table.has(node.id)
            rt_node = self.routing_table.add(node)

            if not existed and rt_node:
                self.logger.debug('Added node %s to the routing table', node)
                # Ping the node in order to determine RTT
                self.ping(rt_node)

    def ping(self, node):
        self.logger.debug('Pinging node %s', node)

        cache = self.request_cache.add(Request(self, u'ping', node, consume_errors=True))
        self.send_message(node.address, MSG_PING, PingRequestPayload, (cache.number,))
        return cache.future

    @lazy_wrapper_wd(GlobalTimeDistributionPayload, PingRequestPayload)
    def on_ping_request(self, peer, dist, payload, data):
        self.logger.debug('Got ping-request from %s', peer.address)

        node = self.get_requesting_node(peer)
        if not node:
            return

        self.send_message(peer.address, MSG_PONG, PingResponsePayload, (payload.identifier,))

    @lazy_wrapper_wd(GlobalTimeDistributionPayload, PingResponsePayload)
    def on_ping_response(self, peer, dist, payload, data):
        if not self.request_cache.has(u'ping', payload.identifier):
            self.logger.error('Got ping-response with unknown identifier, dropping packet')
            return

        self.logger.debug('Got ping-response from %s', peer.address)
        cache = self.request_cache.pop(u'ping', payload.identifier)
        cache.on_complete()
        if not cache.future.done():
            cache.future.set_result(cache.node)

    def serialize_value(self, data, sign=True):
        if sign:
            payload = SignedStrPayload(data, int(time.time()), self.my_peer.public_key.key_to_bin())
            return self._ez_pack(b'', DHT_ENTRY_STR_SIGNED, [payload.to_pack_list()], sig=True)
        payload = StrPayload(data)
        return self._ez_pack(b'', DHT_ENTRY_STR, [payload.to_pack_list()], sig=False)

    def unserialize_value(self, value):
        if ord(value[0:1]) == DHT_ENTRY_STR:
            payload = self.serializer.unpack_to_serializables([StrPayload], value[1:])[0]
            return payload.data, None, 0
        elif ord(value[0:1]) == DHT_ENTRY_STR_SIGNED:
            payload = self.serializer.unpack_to_serializables([SignedStrPayload], value[1:])[0]
            public_key = self.crypto.key_from_public_bin(payload.public_key)
            sig_len = self.crypto.get_signature_length(public_key)
            sig = value[-sig_len:]
            if self.crypto.is_valid_signature(public_key, value[:-sig_len], sig):
                return payload.data, payload.public_key, payload.version

    def add_value(self, key, value, max_age=MAX_ENTRY_AGE):
        unserialized = self.unserialize_value(value)
        if unserialized:
            _, public_key, version = unserialized
            id_ = hashlib.sha1(public_key).digest() if public_key else None
            self.storage.put(key, value, id_=id_, version=version, max_age=max_age)
        else:
            self.logger.warning('Failed to store value %s', hexlify(value))

    async def store_value(self, key, data, sign=False):
        value = self.serialize_value(data, sign=sign)
        return await self._store(key, value)

    async def _store(self, key, value):
        if len(value) > MAX_ENTRY_SIZE:
            raise DHTError('Maximum length exceeded')

        nodes = await self.find_nodes(key)
        nodes = await self.store_on_nodes(key, [value], nodes[:TARGET_NODES])
        if len(nodes) < 1:
            raise DHTError('Failed to store value on DHT')
        return nodes

    @task
    async def store_on_nodes(self, key, values, nodes):
        if not nodes:
            raise DHTError('No nodes found for storing the key-value pairs')

        values = values[:MAX_VALUES_IN_STORE]

        # Check if we also need to store this key-value pair
        largest_distance = max([distance(node.id, key) for node in nodes])
        if len(nodes) < TARGET_NODES or distance(self.my_node_id, key) < largest_distance:
            for value in reversed(values):
                self.add_value(key, value)

        now = time.time()
        futures = []
        for node in nodes:
            if node in self.tokens and self.tokens[node][0] + TOKEN_EXPIRATION_TIME > now:
                cache = self.request_cache.add(Request(self, u'store', node))
                futures.append(cache.future)
                self.send_message(node.address, MSG_STORE_REQUEST, StoreRequestPayload,
                                  (cache.number, self.tokens[node][1], key, values))
            else:
                self.logger.debug('Not sending store-request to %s (no token available)', node)

        if not futures:
            raise DHTError('Value was not stored')
        return await gather_without_errors(*futures)

    @lazy_wrapper(GlobalTimeDistributionPayload, StoreRequestPayload)
    def on_store_request(self, peer, dist, payload):
        self.logger.debug('Got store-request from %s', peer.address)

        node = self.get_requesting_node(peer)
        if not node:
            return
        if any([len(value) > MAX_ENTRY_SIZE for value in payload.values]):
            self.logger.warning('Maximum length of value exceeded, dropping packet.')
            return
        if len(payload.values) > MAX_VALUES_IN_STORE:
            self.logger.warning('Too many values, dropping packet.')
            return
        # Note that even though we are preventing spoofing of source_address (by checking the token),
        # the value that is to be stored isn't checked. This should be done at a higher level.
        if not self.check_token(node, payload.token):
            self.logger.warning('Bad token, dropping packet.')
            return

        # How many nodes (that we know of) are closer to this value?
        num_closer = 0
        for node in self.routing_table.closest_nodes(payload.target, max_nodes=20):
            if distance(node.id, payload.target) < distance(self.my_node_id, payload.target):
                num_closer += 1

        # To prevent over-caching, the expiration time of an entry depends on the number
        # of nodes that are closer than us.
        max_age = MAX_ENTRY_AGE // 2 ** max(0, num_closer - TARGET_NODES + 1)
        for value in payload.values:
            self.add_value(payload.target, value, max_age)

        self.send_message(peer.address, MSG_STORE_RESPONSE, StoreResponsePayload, (payload.identifier,))

    @lazy_wrapper(GlobalTimeDistributionPayload, StoreResponsePayload)
    def on_store_response(self, peer, dist, payload):
        if not self.request_cache.has(u'store', payload.identifier):
            self.logger.error('Got store-response with unknown identifier, dropping packet')
            return

        self.logger.debug('Got store-response from %s', peer.address)
        cache = self.request_cache.pop(u'store', payload.identifier)
        cache.on_complete()
        if not cache.future.done():
            cache.future.set_result(cache.node)

    def _send_find_request(self, node, target, force_nodes, start_idx=0):
        cache = self.request_cache.add(Request(self, u'find', node, [force_nodes]))
        self.send_message(node.address, MSG_FIND_REQUEST, FindRequestPayload,
                          (cache.number, self.my_estimated_lan, target, start_idx, force_nodes))
        return cache.future

    def _process_find_responses(self, responses, nodes_tried):
        values = []
        nodes = set()
        to_puncture = {}

        for sender, response in responses:
            if 'values' in response:
                values.append(response['values'])
            else:
                # Pick a node that we haven't tried yet.
                node = next((n for n in response['nodes']
                             if n not in nodes_tried and n not in list(to_puncture.values())), None)

                if node:
                    nodes.add(node)

                    # If we picked any node other than the first one, we will need to puncture.
                    if node != response['nodes'][0]:
                        to_puncture[sender] = node

        return values, nodes, to_puncture

    async def _find(self, target, force_nodes=False, start_idx=0):
        assert start_idx >= 0, "The query cannot start from a negative index"

        nodes_closest = set(self.routing_table.closest_nodes(target, max_nodes=MAX_FIND_WALKS))
        if not nodes_closest:
            raise DHTError('No nodes found in the routing table')

        nodes_tried = set()
        values = []
        recent = None

        for _ in range(MAX_FIND_STEPS):
            if not nodes_closest:
                break

            # Send closest nodes a find-node-request
            futures = [self._send_find_request(node, target, force_nodes, start_idx=start_idx)
                       for node in nodes_closest]
            responses = await gather_without_errors(*futures)
            recent = next((sender for sender, response in responses if 'nodes' in response), recent)

            nodes_tried |= nodes_closest
            nodes_closest.clear()

            # Process responses and puncture nodes that we haven't tried yet
            new_values, new_nodes, to_puncture = self._process_find_responses(responses, nodes_tried)
            values += new_values
            nodes_closest |= new_nodes

            futures = [self._send_find_request(sender, node.id, force_nodes)
                       for sender, node in to_puncture.items()]

            # Wait for punctures (if any)...
            await gather(*futures, return_exceptions=True)

            # Ensure we haven't tried these nodes yet
            nodes_closest -= nodes_tried
            # Only consider top-k closest to our target
            if len(nodes_closest) > MAX_FIND_WALKS:
                nodes_closest = set(sorted(nodes_closest, key=lambda n: distance(n.id, target))[:MAX_FIND_WALKS])

        if force_nodes:
            return sorted(nodes_tried, key=lambda n: distance(n.id, target))

        # Merge all values received into one tuple. First pick the first value from each tuple, then the second, etc.
        values = sum(zip_longest(*values), ())

        # Filter out duplicates while preserving order
        seen = set()
        values = [v for v in values if v is not None and not (v in seen or seen.add(v))]

        if recent and values:
            # Store the key-value pair on the most recently visited node that
            # did not have it (for caching purposes).
            self.store_on_nodes(target, values, [recent])

        return self.post_process_values(values)

    def post_process_values(self, values):
        # Unpack values and filter out duplicates
        unpacked = defaultdict(list)
        for value in values:
            unserialized = self.unserialize_value(value)
            if unserialized:
                data, public_key, version = unserialized
                unpacked[public_key].append((version, data))

        results = []

        # Signed data
        for public_key, data_list in unpacked.items():
            if public_key is not None:
                results.append((max(data_list, key=lambda t: t[0])[1], public_key))

        # Unsigned data
        for data in unpacked[None]:
            results.append((data[1], None))

        return results

    def find_values(self, target, start_idx=0):
        return self._find(target, force_nodes=False, start_idx=start_idx)

    def find_nodes(self, target):
        return self._find(target, force_nodes=True)

    @lazy_wrapper(GlobalTimeDistributionPayload, FindRequestPayload)
    def on_find_request(self, peer, dist, payload):
        self.logger.debug('Got find-request for %s from %s', hexlify(payload.target), peer.address)

        node = self.get_requesting_node(peer)
        if not node:
            return

        nodes = []
        values = self.storage.get(payload.target, starting_point=payload.start_idx, limit=MAX_VALUES_IN_FIND) \
            if not payload.force_nodes else []

        if payload.force_nodes or not values:
            nodes = self.routing_table.closest_nodes(payload.target, exclude_node=node, max_nodes=MAX_NODES_IN_FIND)
            # Send puncture request to the closest node
            if nodes:
                packet = self.create_puncture_request(payload.lan_address, peer.address, payload.identifier)
                self.endpoint.send(nodes[0].address, packet)

        self.send_message(peer.address, MSG_FIND_RESPONSE, FindResponsePayload,
                          (payload.identifier, self.generate_token(node), values, nodes))

    @lazy_wrapper(GlobalTimeDistributionPayload, FindResponsePayload)
    def on_find_response(self, peer, dist, payload):
        if not self.request_cache.has(u'find', payload.identifier):
            self.logger.error('Got find-response with unknown identifier, dropping packet')
            return

        self.logger.debug('Got find-response from %s', peer.address)
        cache = self.request_cache.pop(u'find', payload.identifier)
        cache.on_complete()

        self.tokens[cache.node] = (time.time(), payload.token)

        if cache.future.done():
            # The errback must already have been called (due to a timeout)
            return
        elif cache.params[0]:
            cache.future.set_result((cache.node, {'nodes': payload.nodes}))
        else:
            cache.future.set_result((cache.node,
                                     {'values': payload.values} if payload.values else {'nodes': payload.nodes}))

    async def value_maintenance(self):
        # Refresh buckets
        now = time.time()
        for bucket in self.routing_table.trie.values():
            if now - bucket.last_changed > 15 * 60:
                try:
                    await self.find_values(bucket.generate_id())
                except DHTError:
                    pass
                bucket.last_changed = now

        # FIXME: Disable replication for now, as it creates too much traffic
        # for key, value in self.storage.items_older_than(3600):
        #    self._store(key, value).addErrback(lambda _: None)

        # Also republish our own key-value pairs every 24h?

    def token_maintenance(self):
        self.token_secrets.append(os.urandom(16))

        # Cleanup old tokens
        now = time.time()
        for node, (ts, _) in list(self.tokens.items()):
            if now > ts + TOKEN_EXPIRATION_TIME:
                self.tokens.pop(node, None)

    def generate_token(self, node):
        return hashlib.sha1(cast_to_bin(str(node)) + self.token_secrets[-1]).digest()

    def check_token(self, node, token):
        return any([hashlib.sha1(cast_to_bin(str(node)) + secret).digest() == token for secret in self.token_secrets])
