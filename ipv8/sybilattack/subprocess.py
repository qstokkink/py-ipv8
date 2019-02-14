from twisted.internet import protocol


class Subprocess(protocol.ProcessProtocol):

    def __init__(self):
        self.data = ""
        self.done = False

    def connectionMade(self):
        pass

    def outReceived(self, data):
        self.data = self.data + data

    def errReceived(self, data):
        print "Error in Subprocess:", data

    def inConnectionLost(self):
        pass

    def outConnectionLost(self):
        pass

    def errConnectionLost(self):
        pass

    def processExited(self, reason):
        pass

    def processEnded(self, reason):
        self.done = True
