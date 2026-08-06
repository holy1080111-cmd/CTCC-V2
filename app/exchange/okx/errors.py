class OkxPublicApiError(RuntimeError):
    """Raised when the OKX public API returns an invalid or failed response."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class OkxPrivateApiError(RuntimeError):
    """Raised for authenticated OKX Demo REST failures without exposing secrets."""

    def __init__(self, message: str, *, code: str | None = None, data=None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data
