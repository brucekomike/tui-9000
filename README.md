# tui-9000

An nmtui-style terminal interface for a local [Mihomo](https://github.com/MetaCubeX/mihomo) kernel. By default, tui-9000 connects to the external control API at `127.0.0.1:9097` (not the proxy traffic port).

The controller client uses the [Mihomo REST API](https://wiki.metacubex.one/en/api/) and its `Authorization: Bearer <secret>` authentication.

## Features

- Connect to Clash Verge Rev, Pure Clash, or another Mihomo-compatible controller.
- View the controller version, current routing mode, and mixed port.
- From a proxy group's member submenu, choose to test that node's delay or select it; also view current upload/download rates and totals, switch routing modes, and inspect or close active connections.
- Optionally validate a Mihomo YAML config and start/stop a kernel managed by this app.

## Requirements

- Python 3.10 or Bash 4+
- `whiptail` (or install a package that provides it)
- `curl` and `jq` for the Bash version
- A Mihomo-compatible controller for the client features; `mihomo` is only needed for the optional managed-kernel feature.

On Debian/Ubuntu, install the UI dependencies with `sudo apt install whiptail curl jq`.

## Run

```sh
python3 tui9000.py
```

Or use the Bash implementation:

```sh
./tui9000.sh
```

Both versions use whiptail for the nmtui-style interface and share the same settings file.

Pass a Bearer secret on the command line to override the saved secret for that run:

```sh
python3 tui9000.py --secret 'your-controller-secret'
# Short form:
python3 tui9000.py -s 'your-controller-secret'
./tui9000.sh --secret 'your-controller-secret'
./tui9000.sh -s 'your-controller-secret'
```

The override is not saved to settings. To use a literal secret beginning with a dash, pass it as `--secret='-your-secret'`.

### Clash Verge Rev

In Clash Verge Rev, enable **Settings → External Controller**, set its address (normally `127.0.0.1:9097`) and secret, then save. The controller secret is separate from the proxy username/password. Clash Verge Rev does not expose this HTTP controller until the External Controller setting is enabled.

Open **Connection and kernel settings** in tui-9000 and enter the same address and secret. When configured, tui-9000 sends the secret as `Authorization: Bearer <secret>`, as required by Mihomo. `curl -u` sends HTTP Basic authentication and is not the right way to authenticate to this API. For example, if the configured secret is `0000`:

```sh
curl -H 'Authorization: Bearer 0000' http://127.0.0.1:9097/version
```

The secret is saved in `~/.config/tui-9000/settings.json`, with file permissions restricted to your user. The controller must listen on an address reachable from this machine; for a different port or address, update tui-9000 Settings to match. The controller's secret must match the `secret` configured alongside `external-controller` in Clash Verge Rev, Pure Clash, or Mihomo.

## Optional managed kernel

Set the Mihomo executable and a YAML config path under Settings, then choose **Start / stop local Mihomo**. tui-9000 validates the config with Mihomo before launching it. Its API address and secret are configured in that YAML file (for example, `external-controller: 127.0.0.1:9097`); set the matching address and secret in tui-9000 Settings to control it. The managed kernel runs independently and stays running after the UI exits. Its log is at `~/.local/share/tui-9000/mihomo.log`.

## Tests

```sh
python3 -m unittest discover -s tests
```
