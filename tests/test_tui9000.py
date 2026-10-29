import json
import os
import unittest
from unittest.mock import MagicMock, patch

from tui9000 import App, DEFAULT_ENDPOINT, Dialog, MihomoApi, normalize_endpoint


class EndpointTests(unittest.TestCase):
    def test_normalizes_host_port(self):
        self.assertEqual(normalize_endpoint(" 127.0.0.1:9097/ "), "http://127.0.0.1:9097")

    def test_default_endpoint_uses_mihomo_port(self):
        self.assertEqual(DEFAULT_ENDPOINT, "http://127.0.0.1:9097")

    def test_keeps_https(self):
        self.assertEqual(normalize_endpoint("https://mihomo.local:9097/"), "https://mihomo.local:9097")

    def test_rejects_invalid_endpoint(self):
        with self.assertRaises(ValueError):
            normalize_endpoint("not an endpoint")


class ApiTests(unittest.TestCase):
    @patch("tui9000.urllib.request.urlopen")
    def test_adds_bearer_secret_and_decodes_json(self, urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps({"version": "1.19.0"}).encode()
        response.__enter__.return_value = response
        urlopen.return_value = response

        result = MihomoApi("127.0.0.1:9097", "secret").version()

        request = urlopen.call_args.args[0]
        self.assertEqual(result["version"], "1.19.0")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(request.full_url, "http://127.0.0.1:9097/version")

    @patch("tui9000.urllib.request.urlopen")
    def test_quotes_proxy_group_path_and_sends_selection(self, urlopen):
        response = MagicMock()
        response.read.return_value = b""
        response.__enter__.return_value = response
        urlopen.return_value = response

        MihomoApi("127.0.0.1:9097").select_proxy("Auto / Main", "node 1")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:9097/proxies/Auto%20%2F%20Main")
        self.assertEqual(json.loads(request.data), {"name": "node 1"})


class AppMenuTests(unittest.TestCase):
    def test_cli_secret_overrides_saved_secret(self):
        app = App.__new__(App)
        app.settings = {"endpoint": DEFAULT_ENDPOINT, "secret": "saved-secret"}
        app.secret_override = "cli-secret"

        self.assertEqual(app.api().secret, "cli-secret")

    @patch("tui9000.subprocess.run")
    def test_dialog_captures_selection_on_separate_fd(self, run):
        def write_selection(arguments, **kwargs):
            output_fd = int(arguments[arguments.index("--output-fd") + 1])
            os.write(output_fd, b"status\n")
            return MagicMock(returncode=0)

        run.side_effect = write_selection

        code, selection = Dialog("whiptail").run("--menu", "Choose", "10", "40", "2")

        self.assertEqual((code, selection), (0, "status"))
        self.assertNotIn("stdout", run.call_args.kwargs)
        self.assertIn(int(run.call_args.args[0][run.call_args.args[0].index("--output-fd") + 1]), run.call_args.kwargs["pass_fds"])

    def test_unexpected_menu_tag_does_not_crash_dispatch(self):
        class FakeDialog:
            def __init__(self):
                self.selections = iter(("unexpected", None))
                self.messages = []

            def menu(self, *_args, **_kwargs):
                return next(self.selections)

            def message(self, text, *_args, **_kwargs):
                self.messages.append(text)

        app = App.__new__(App)
        app.dialog = FakeDialog()
        app.settings = {"endpoint": DEFAULT_ENDPOINT}

        app.run()

        self.assertEqual(len(app.dialog.messages), 1)
        self.assertIn("Unexpected menu selection", app.dialog.messages[0])


if __name__ == "__main__":
    unittest.main()
