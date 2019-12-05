import itertools
import math
import time


def test_peers(ping_func, peer1, peer2):
    first_peer = peer1.get_median_ping() > peer2.get_median_ping()
    if not first_peer:
        peer1, peer2 = peer2, peer1
    nonces = []
    ping_window_repeats = max(1, int(math.ceil(peer1.get_median_ping() / 0.2)))
    for _ in xrange(ping_window_repeats):
        for _ in xrange(20):
            nonces.append(ping_func(peer1))
        for _ in xrange(20):
            ping_func(peer2)
    return peer1, nonces


def trendline_f(simple, series, ((x_min, y_min), (x_max, y_max))):
    if simple:
        return lambda x: (y_max - y_min)/(x_max - x_min) + y_min
    else:
        last_ping_time = series[0][1]
        last_diff = 0
        for start_time, ping_time in series[1:]:
            this_diff = ping_time - last_ping_time
            if this_diff < last_diff:
                return lambda x: (ping_time - y_min)/(start_time - x_min) + y_min
            last_ping_time = ping_time
            last_diff = this_diff
        return trendline_f(True, series, ((x_min, y_min), (x_max, y_max)))


def get_measure_min_max(series):
    measure_min = series[0]
    measure_max = series[-1]
    x_min = measure_min[0]
    x_max = measure_max[0]
    y_min = measure_min[1]
    y_max = measure_max[1]
    return (x_min, y_min), (x_max, y_max)


def sybil_score(series):
    clean_series = [(t[0], t[1]) for t in series if t[1] != -1]
    if len(clean_series) <= 1:
        return None
    clean_series.sort(key=lambda x: x[0])
    trendline = trendline_f(False, clean_series, get_measure_min_max(clean_series))

    mse = 1 / float(len(clean_series)) * sum(math.pow(trendline(clean_series[x][0]) - clean_series[x][1], 2) for x in xrange(len(clean_series)))

    return mse  # < 10.0 == Sybil


HEAD_COUNT = 6
DELTA = 0.05


def is_distinct(peer, others):
    if peer.get_median_ping() is None:
        print "No median ping for", peer
        return False
    return all(abs(p.get_median_ping() - peer.get_median_ping()) > DELTA for p in others)


def remove_node(peer, heads, ancestry):
    new_head = peer in heads
    tails = {}

    for head in heads:
        # Loop through edges
        previous2 = None
        previous = None
        current = head
        do_del = False
        while current is not None and current in ancestry:
            if not do_del:
                previous2 = previous
                previous = current
            if current == peer:
                do_del = True
            if do_del:
                current = ancestry.pop(current, None)
            else:
                current = ancestry.get(current, None)
        if previous is not None:
            tails[previous] = previous2

    if peer in heads:
        heads.remove(peer)
    if peer in tails:
        tails.pop(peer)

    return new_head, tails


def create_topology(bootstrap_func, walk_func, ping_func, get_ping_func, update_rate=0.5, experiment_time=60.0):
    # Create topology: we can actively sleep here, it's in a thread
    blacklist = []
    heads = set()  # Root nodes
    tails = {}  # Tail node: parent
    ancestry = {}  # Node: child
    mse_history = {}  # (a,b): score

    output = []

    experiment_end_time = time.time() + experiment_time

    while len(heads) < HEAD_COUNT and time.time() < experiment_end_time:
        peer = bootstrap_func()
        if is_distinct(peer, heads):
            heads.add(peer)
            tails[peer] = None

    output.append((time.time(), list(heads)))

    pending_checks = set(itertools.combinations(heads, 2))
    previous_check = None

    while time.time() < experiment_end_time:
        start_time = time.time()
        peer_count = len(heads) + len(ancestry)

        # Create main ancestry through walking from heads until we have 20 peers
        if peer_count < 20:
            new_tails = {}
            for tail in tails:
                peer = walk_func(tail)
                if peer not in blacklist and peer not in heads and peer not in ancestry.values():
                    ancestry[tail] = peer
                    new_tails[peer] = tail
                    pending_checks.add((tail, peer))
                else:
                    new_tails[tail] = tails[tail]
            tails = new_tails
        else:
            worst = sorted(mse_history.items(), key=lambda x: x[1])[0]
            new_head, tails = remove_node(worst, heads, ancestry)
            blacklist.append(worst)
            if len(blacklist) > 10:
                blacklist.pop(0)
            if new_head:
                peer = bootstrap_func()
                while not is_distinct(peer, heads):
                    peer = bootstrap_func()
                heads.add(peer)
                tails[peer] = None

        # Empty pending_checks queue
        if previous_check is not None:
            peer1, nonces = previous_check
            series = [get_ping_func(peer1, nonce) for nonce in nonces]
            sscore = sybil_score(series)
            if sscore is not None:
                mse_history[peer1] = sscore
            if sscore is not None and sscore < 10.0:
                # Sybils!
                # Remove peer1 and following nodes, add to blacklist - possibly get new bootstrap head
                new_head, tails = remove_node(peer1, heads, ancestry)
                blacklist.append(peer1)
                if len(blacklist) > 10:
                    blacklist.pop(0)
                if new_head:
                    peer = bootstrap_func()
                    while not is_distinct(peer, heads):
                        peer = bootstrap_func()
                    heads.add(peer)
                    tails[peer] = None

            previous_check = None

        if pending_checks:
            previous_check = test_peers(ping_func, *(pending_checks.pop()))

        output.append((time.time(), list(heads) + ancestry.values()))

        sleep_time = update_rate - (time.time() - start_time)
        if sleep_time > 0.01:
            time.sleep(sleep_time)

    return output
