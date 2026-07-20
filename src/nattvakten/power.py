import subprocess


class PowerController:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def power_off(self) -> None:
        if not self._enabled:
            return
        subprocess.run(
            [
                "/usr/bin/busctl",
                "--address=unix:path=/run/dbus/system_bus_socket",
                "call",
                "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1",
                "org.freedesktop.systemd1.Manager",
                "StartUnit",
                "ss",
                "nattvakten-poweroff.service",
                "replace",
            ],
            check=True,
        )
