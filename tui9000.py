#!/usr/bin/env python3
"""A small whiptail-based terminal client for the Mihomo REST API."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


APP_NAME = "tui-9000"
DEFAULT_ENDPOINT = "http://127.0.0.1:9097"


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_NAME


def normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    if not endpoint:
        raise ValueError("API address cannot be empty")
    if any(character.isspace() for character in endpoint):
        raise ValueError("Controller address cannot contain spaces")
    if "://" not in endpoint:
        endpoint = "http://" + endpoint
    parsed = urllib.parse.urlsplit(endpoint)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Controller address contains an invalid port") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter an HTTP(S) address, such as 127.0.0.1:9097")
    return endpoint


class MihomoApi:
    def __init__(self, endpoint: str, secret: str = "", timeout: float = 3.0):
        self.endpoint = normalize_endpoint(endpoint)
        self.secret = secret
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        request = urllib.request.Request(
            self.endpoint + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            if exc.code in (401, 403):
                raise RuntimeError("Mihomo rejected the secret. Check Settings.") from exc
            raise RuntimeError(f"Mihomo API returned HTTP {exc.code}: {detail or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise RuntimeError(f"Cannot reach {self.endpoint}: {reason}") from exc
        if not payload:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Mihomo returned an unexpected response") from exc

    def version(self) -> dict[str, Any]:
        return self.request("GET", "/version")

    def config(self) -> dict[str, Any]:
        return self.request("GET", "/configs")

    def proxies(self) -> dict[str, Any]:
        return self.request("GET", "/proxies")

    def select_proxy(self, group: str, member: str) -> None:
        group_path = urllib.parse.quote(group, safe="")
        self.request("PUT", f"/proxies/{group_path}", {"name": member})

    def set_mode(self, mode: str) -> None:
        self.request("PATCH", "/configs", {"mode": mode})

    def connections(self) -> dict[str, Any]:
        return self.request("GET", "/connections")

    def close_connection(self, connection_id: str) -> None:
        self.request("DELETE", f"/connections/{urllib.parse.quote(connection_id, safe='')}")


class Dialog:
    def __init__(self, executable: str):
        self.executable = executable

    def run(self, *arguments: str) -> tuple[int, str]:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
            result = subprocess.run(
                [
                    self.executable,
                    "--title",
                    "tui-9000 · Mihomo",
                    "--output-fd",
                    str(output.fileno()),
                    *arguments,
                ],
                pass_fds=(output.fileno(),),
                check=False,
            )
            output.seek(0)
            return result.returncode, output.read().strip()

    def message(self, text: str, height: int = 12, width: int = 72) -> None:
        self.run("--msgbox", text, str(height), str(width))

    def menu(self, text: str, options: list[tuple[str, str]], height: int = 20) -> str | None:
        args = ["--menu", text, str(height), "78", str(max(5, min(len(options), 12)))]
        for tag, label in options:
            args.extend((tag, label))
        code, selection = self.run(*args)
        return selection if code == 0 else None

    def input(self, prompt: str, current: str = "", secret: bool = False) -> str | None:
        box = "--passwordbox" if secret else "--inputbox"
        code, value = self.run(box, prompt, "10", "72", current)
        return value if code == 0 else None

    def confirm(self, prompt: str) -> bool:
        code, _ = self.run("--yesno", prompt, "10", "72")
        return code == 0


class App:
    def __init__(self, dialog: Dialog, secret_override: str | None = None):
        self.dialog = dialog
        self.secret_override = secret_override
        self.settings_path = config_dir() / "settings.json"
        self.settings = self.load_settings()
        self.kernel_process: subprocess.Popen[bytes] | None = None

    def load_settings(self) -> dict[str, str]:
        defaults = {"endpoint": DEFAULT_ENDPOINT, "secret": "", "mihomo_binary": "mihomo", "config_file": ""}
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                defaults.update({key: str(value) for key, value in saved.items() if key in defaults})
        except (OSError, json.JSONDecodeError):
            pass
        return defaults

    def save_settings(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(self.settings, indent=2) + "\n", encoding="utf-8")
        self.settings_path.chmod(0o600)

    def api(self) -> MihomoApi:
        secret = self.secret_override if self.secret_override is not None else self.settings["secret"]
        return MihomoApi(self.settings["endpoint"], secret)

    def run(self) -> None:
        while True:
            endpoint = self.settings["endpoint"]
            choice = self.dialog.menu(
                f"Controller: {endpoint}\n\nUse ↑/↓ and Enter to choose an action.",
                [
                    ("status", "Test controller / connection status"),
                    ("proxies", "Proxy groups"),
                    ("connections", "Active connections"),
                    ("mode", "Routing mode"),
                    ("kernel", "Start / stop local Mihomo"),
                    ("settings", "Connection and kernel settings"),
                    ("exit", "Exit tui-9000"),
                ],
            )
            if choice is None or choice == "exit":
                return
            actions = {
                "status": self.show_status,
                "proxies": self.proxy_groups,
                "connections": self.connections,
                "mode": self.routing_mode,
                "kernel": self.kernel_menu,
                "settings": self.settings_menu,
            }
            action = actions.get(choice)
            if action is None:
                self.dialog.message(f"Unexpected menu selection: {choice!r}. Please try again.")
                continue
            try:
                action()
            except (RuntimeError, ValueError, OSError) as exc:
                self.dialog.message(str(exc))

    def show_status(self) -> None:
        api = self.api()
        version = api.version()
        config = api.config()
        self.dialog.message(
            "Connected to Mihomo\n\n"
            f"API: {api.endpoint}\n"
            f"Version: {version.get('version', 'unknown')}\n"
            f"Mode: {config.get('mode', 'unknown')}\n"
            f"Mixed port: {config.get('mixed-port', 'not reported')}\n"
            f"Managed kernel: {'running' if self.managed_kernel_running() else 'not running'}",
            14,
            72,
        )

    def proxy_groups(self) -> None:
        proxies = self.api().proxies().get("proxies", {})
        groups = [
            (name, proxy)
            for name, proxy in proxies.items()
            if proxy.get("type") in {"Selector", "URLTest", "Fallback", "LoadBalance", "Relay"}
            and proxy.get("all")
        ]
        if not groups:
            self.dialog.message("No selectable proxy groups were found.")
            return
        group_name = self.dialog.menu("Select a proxy group:", [(name, proxy.get("now", "")) for name, proxy in groups])
        if group_name is None:
            return
        group = proxies[group_name]
        members = [(member, "● current" if member == group.get("now") else "") for member in group["all"]]
        member = self.dialog.menu(f"{group_name} · current: {group.get('now', 'unknown')}\n\nChoose a proxy:", members)
        if member is not None and member != group.get("now"):
            self.api().select_proxy(group_name, member)
            self.dialog.message(f"{group_name} is now using:\n\n{member}")

    @staticmethod
    def connection_label(connection: dict[str, Any]) -> str:
        metadata = connection.get("metadata", {})
        destination = metadata.get("host") or metadata.get("destinationIP", "unknown destination")
        process = metadata.get("process") or metadata.get("processPath") or "unknown process"
        network = metadata.get("network", "?")
        chains = " → ".join(connection.get("chains", [])) or "DIRECT"
        return f"{process} · {destination} ({network}) · {chains}"[:180]

    def connections(self) -> None:
        data = self.api().connections()
        connections = data.get("connections", [])
        if not connections:
            self.dialog.message("There are no active connections.")
            return
        by_id = {item.get("id", ""): item for item in connections}
        options = [(item.get("id", ""), self.connection_label(item)) for item in connections if item.get("id")]
        selected = self.dialog.menu(f"{len(connections)} active connection(s). Select one to close:", options, 24)
        if selected is None:
            return
        if self.dialog.confirm(f"Close this connection?\n\n{self.connection_label(by_id[selected])}"):
            self.api().close_connection(selected)
            self.dialog.message("Connection closed.")

    def routing_mode(self) -> None:
        api = self.api()
        current = api.config().get("mode", "unknown")
        modes = [(mode, "● current" if mode == current else "") for mode in ("rule", "global", "direct")]
        selected = self.dialog.menu(f"Current mode: {current}\n\nSelect a routing mode:", modes)
        if selected is not None and selected != current:
            api.set_mode(selected)
            self.dialog.message(f"Routing mode set to {selected}.")

    def settings_menu(self) -> None:
        while True:
            choice = self.dialog.menu(
                "For Clash Verge Rev, enable Settings → External Controller first.\n"
                "Saved locally in ~/.config/tui-9000/settings.json (secret file mode 0600).",
                [
                    ("endpoint", f"Controller address · {self.settings['endpoint']}"),
                    ("secret", f"Bearer API secret · {'supplied on command line' if self.secret_override is not None else ('configured' if self.settings['secret'] else 'not set')}"),
                    ("binary", f"Mihomo binary · {self.settings['mihomo_binary']}"),
                    ("config", f"Mihomo config · {self.settings['config_file'] or 'not set'}"),
                    ("back", "Back"),
                ],
            )
            if choice is None or choice == "back":
                return
            keys = {"endpoint": "endpoint", "secret": "secret", "binary": "mihomo_binary", "config": "config_file"}
            prompts = {
                "endpoint": ("Controller address (host:port or URL):", False),
                "secret": ("Controller secret / Bearer token (from Clash Verge Rev External Controller):", True),
                "binary": ("Mihomo executable path or command name:", False),
                "config": ("Path to Mihomo YAML config file:", False),
            }
            prompt, hidden = prompts[choice]
            current_value = self.settings[keys[choice]]
            if choice == "secret" and self.secret_override is not None:
                current_value = self.secret_override
            value = self.dialog.input(prompt, current_value, hidden)
            if value is None:
                continue
            if choice == "endpoint":
                try:
                    value = normalize_endpoint(value)
                except ValueError as exc:
                    self.dialog.message(str(exc))
                    continue
            self.settings[keys[choice]] = value.strip()
            if choice == "secret":
                self.secret_override = None
            self.save_settings()

    def resolve_binary(self) -> str:
        configured = self.settings["mihomo_binary"].strip()
        binary = shutil.which(configured) if configured else None
        configured_path = Path(configured).expanduser() if configured else None
        if not binary and configured_path and configured_path.is_file():
            binary = str(configured_path.resolve())
        if not binary:
            raise RuntimeError(f"Mihomo executable not found: {configured or '(empty)'}")
        return str(Path(binary).resolve())

    @staticmethod
    def process_matches(pid: int, binary: str, config_file: str, home: str) -> bool:
        """Check the Linux process identity before signaling a saved PID."""
        proc = Path("/proc") / str(pid)
        try:
            executable = os.path.realpath(proc / "exe")
            arguments = (proc / "cmdline").read_bytes().decode(errors="replace").split("\0")
            arguments = [arg for arg in arguments if arg]
            return (
                executable == os.path.realpath(binary)
                and "-f" in arguments
                and config_file in arguments
                and "-d" in arguments
                and home in arguments
            )
        except OSError:
            return False

    def managed_process(self) -> dict[str, Any] | None:
        state_file = data_dir() / "kernel.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            pid = int(state["pid"])
            binary = str(state["binary"])
            config_file = str(state["config_file"])
            home = str(state["home"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
        return state if self.process_matches(pid, binary, config_file, home) else None

    def managed_kernel_running(self) -> bool:
        if self.kernel_process is not None and self.kernel_process.poll() is None:
            return True
        return self.managed_process() is not None

    def start_kernel(self) -> None:
        binary = self.resolve_binary()
        config_file = Path(self.settings["config_file"]).expanduser().resolve()
        if not config_file.is_file():
            raise RuntimeError("Set a valid Mihomo config file in Settings before starting the kernel.")
        if self.managed_kernel_running():
            raise RuntimeError("A kernel started by tui-9000 is already running.")
        home = data_dir() / "kernel-home"
        home.mkdir(parents=True, exist_ok=True)
        validation = subprocess.run(
            [binary, "-t", "-f", str(config_file), "-d", str(home)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if validation.returncode:
            raise RuntimeError("Mihomo config validation failed:\n" + validation.stdout[-2500:])
        log_path = data_dir() / "mihomo.log"
        with log_path.open("ab") as log_file:
            self.kernel_process = subprocess.Popen(
                [binary, "-f", str(config_file), "-d", str(home)],
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        state = {
            "pid": self.kernel_process.pid,
            "binary": binary,
            "config_file": str(config_file),
            "home": str(home.resolve()),
        }
        state_path = data_dir() / "kernel.json"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        state_path.chmod(0o600)
        self.dialog.message(
            f"Mihomo started (PID {self.kernel_process.pid}).\n\n"
            f"Log: {log_path}\n\n"
            "The API address and secret in Settings must match external-controller and secret in the config."
        )

    def stop_kernel(self) -> None:
        state = self.managed_process()
        if state is None:
            raise RuntimeError("No running kernel started by tui-9000 was found.")
        pid = int(state["pid"])
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.process_matches(
            pid, state["binary"], state["config_file"], state["home"]
        ):
            time.sleep(0.1)
        if self.process_matches(pid, state["binary"], state["config_file"], state["home"]):
            os.kill(pid, signal.SIGKILL)
        try:
            (data_dir() / "kernel.json").unlink()
        except FileNotFoundError:
            pass
        self.kernel_process = None
        self.dialog.message("Managed Mihomo kernel stopped.")

    def kernel_menu(self) -> None:
        running = self.managed_kernel_running()
        choice = self.dialog.menu(
            f"Managed kernel: {'running' if running else 'stopped'}\n"
            f"Binary: {self.settings['mihomo_binary']}\n"
            f"Config: {self.settings['config_file'] or 'not set'}\n\n"
            "A managed kernel stays running after tui-9000 exits.",
            [("stop", "Stop managed kernel")] if running else [("start", "Validate config and start kernel")],
        )
        if choice == "start":
            self.start_kernel()
        elif choice == "stop" and self.dialog.confirm("Stop the Mihomo kernel started by tui-9000?"):
            self.stop_kernel()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="nmtui-style Mihomo controller")
    parser.add_argument(
        "-s",
        "--secret",
        help="use this Mihomo Bearer-token secret for this run (overrides the saved secret)",
    )
    arguments = parser.parse_args(argv)
    executable = shutil.which("whiptail")
    if not executable:
        print("tui-9000 requires whiptail. Install whiptail or use your system's dialog package.", file=sys.stderr)
        return 1
    try:
        App(Dialog(executable), secret_override=arguments.secret).run()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
