"""供应商边界的异常族；各 provider 在边界处把 SDK 异常映射到这里。"""


class ProviderError(Exception):
    """所有供应商错误的基类；status_code 与 body 供工具通道判降级。"""

    def __init__(self, message: str, *, status_code: int | None = None,
                 body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ProviderAuthError(ProviderError):
    pass


class ProviderBadRequest(ProviderError):
    pass


class ProviderNotFound(ProviderError):
    pass


class ProviderRateLimit(ProviderError):
    pass


class ProviderServerError(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


class ProviderConnection(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    """通道不可用：名字未知或依赖缺失；携带可读的修复说明。"""
