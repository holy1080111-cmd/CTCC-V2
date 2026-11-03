"""Source-only stand-in for an imported vendor class; no network or native IO."""


class ExternalStream:
    def send(self, value=None):
        return value

    @classmethod
    def close(cls):
        return None
