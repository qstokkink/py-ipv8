from ipv8.sybilattack.database import TraceDB


database = TraceDB(u"sqlite/tracedb.db")
database.open(prepare_visioning=False)

print "Attacks:"
for result in database.execute("SELECT identifiers.ip, identifiers.port, identifiers.sybil_count, identifiers.type, measurements.ip, measurements.time FROM identifiers JOIN measurements ON identifiers.id == measurements.id"):
    ip, port, sybil_count, measurement_type, intermediate_ip, time = result
    print sybil_count, "identities --", intermediate_ip, "->", ':'.join([ip, str(port)]),\
        "route using", measurement_type, "completed in", time
