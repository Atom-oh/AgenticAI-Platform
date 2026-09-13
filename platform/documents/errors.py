"""Bounded domain errors; messages must never include uploaded text or SDK errors."""


class DocumentError(Exception):
    def __init__(self, status, code, message):
        self.status = status
        self.code = code[:80]
        self.message = message[:300]
        super().__init__(self.message)
