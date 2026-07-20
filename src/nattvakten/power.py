from pathlib import Path


class PowerController:
    def __init__(
        self,
        enabled: bool,
        request_path: str = "/run/nattvakten/poweroff.request",
    ) -> None:
        self._enabled = enabled
        self._request_path = Path(request_path)

    def power_off(self) -> None:
        if not self._enabled:
            return
        self._request_path.touch()
