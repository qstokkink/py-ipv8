import math


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
    clean_series = [(t.start, t.end) for t in series if t.end != -1]
    if len(clean_series) <= 1:
        return None
    clean_series.sort(key=lambda x: x[0])
    trendline = trendline_f(False, clean_series, get_measure_min_max(clean_series))

    mse = 1 / float(len(clean_series)) * sum(math.pow(trendline(clean_series[x][0]) - clean_series[x][1], 2) for x in xrange(len(clean_series)))

    return mse  # < 10.0 == Sybil


def create_topology(bootstrap_func, walk_func, ping_func, get_ping_func, update_rate=0.5, experiment_time=60.0):
    pass # Create topology: we can actively sleep here, it's in a thread
