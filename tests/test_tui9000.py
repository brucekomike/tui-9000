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

    @patch("tui9000.urllib.request.urlopen")
    def test_proxy_delay_quotes_path_and_query(self, urlopen):
        response = MagicMock()
        response.read.return_value = b'{"delay":123}'
        response.__enter__.return_value = response
        urlopen.return_value = response

        delay = MihomoApi("127.0.0.1:9097", "secret").proxy_delay(
            "Auto / Main", "https://example.com/generate_204?a=1&b=two", 5000
        )

        request = urlopen.call_args.args[0]
        self.assertEqual(delay, 123)
        self.assertEqual(
            request.full_url,
            "http://127.0.0.1:9097/proxies/Auto%20%2F%20Main/delay?"
            "url=https%3A%2F%2Fexample.com%2Fgenerate_204%3Fa%3D1%26b%3Dtwo&timeout=5000",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")

    @patch("tui9000.urllib.request.urlopen")
    def test_traffic_reads_first_streamed_sample(self, urlopen):
        response = MagicMock()
        response.readline.return_value = b'{"up":12,"down":34,"upTotal":100,"downTotal":200}\n'
        response.__enter__.return_value = response
        urlopen.return_value = response

        traffic = MihomoApi("127.0.0.1:9097", "secret").traffic()

        self.assertEqual(traffic, {"up": 12, "down": 34, "upTotal": 100, "downTotal": 200})
        response.readline.assert_called_once_with()
        response.read.assert_not_called()


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

    def test_proxy_member_menu_offers_delay_and_select_actions(self):
        class FakeDialog:
            def __init__(self):
                self.selections = iter(("Auto", "Node A", "delay"))
                self.inputs = iter(("https://example.com", "5000"))
                self.menu_prompts = []
                self.messages = []

            def menu(self, prompt, _options, *_args):
                self.menu_prompts.append(prompt)
                return next(self.selections)

            def input(self, *_args):
                return next(self.inputs)

            def message(self, text, *_args):
                self.messages.append(text)

        class FakeApi:
            def __init__(self):
                self.delay_call = None
                self.selection_call = None

            def proxies(self):
                return {"proxies": {"Auto": {"type": "Selector", "now": "Node B", "all": ["Node A", "Node B"]}}}

            def proxy_delay(self, proxy, url, timeout_ms):
                self.delay_call = (proxy, url, timeout_ms)
                return 42

            def select_proxy(self, group, member):
                self.selection_call = (group, member)

        app = App.__new__(App)
        app.dialog = FakeDialog()
        api = FakeApi()
        app.api = lambda: api

        app.proxy_groups()

        self.assertEqual(api.delay_call, ("Node A", "https://example.com", 5000))
        self.assertIsNone(api.selection_call)
        self.assertIn("Choose an action", app.dialog.menu_prompts[-1])
        self.assertIn("Delay: 42 ms", app.dialog.messages[-1])


if __name__ == "__main__":
    unittest.main()
