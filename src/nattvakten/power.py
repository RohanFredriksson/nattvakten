import subprocess


class PowerController:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def power_off(self) -> None:
        if not self._enabled:
            return
        subprocess.run(
            ["/usr/bin/systemctl", "start", "nattvakten-poweroff.service"],
            check=True,
        )
