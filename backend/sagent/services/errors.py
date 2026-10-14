"""服務層的錯誤。API 層會轉成對應的 HTTP 狀態碼。"""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


class BadRequest(ServiceError):
    status_code = 400


class Unavailable(ServiceError):
    """外部服務（Ollama、PlantUML）連不到或沒有設定。"""

    status_code = 503
