class PrivacyFailure(Exception):
    """Only a stable code crosses the private processing boundary."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
