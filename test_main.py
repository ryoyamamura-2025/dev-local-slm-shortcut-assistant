import unittest
from unittest.mock import Mock, patch

import main


class UrlActionTests(unittest.TestCase):
    def test_google_search_encodes_japanese_query(self) -> None:
        self.assertEqual(
            main.google_search("生成 AI"),
            "https://www.google.com/search?q=%E7%94%9F%E6%88%90+AI",
        )

    def test_open_url_adds_https_scheme(self) -> None:
        self.assertEqual(main.open_url("example.com"), "https://example.com")


class RecycleBinActionTests(unittest.TestCase):
    @patch("main.os.startfile")
    def test_open_recycle_bin_uses_shell_folder(self, startfile: Mock) -> None:
        self.assertEqual(main.open_recycle_bin(), "ゴミ箱を開きました。")
        startfile.assert_called_once_with("shell:RecycleBinFolder")

    def test_empty_recycle_bin_requires_confirmation(self) -> None:
        action = main.actions["empty_recycle_bin"]
        function = Mock(return_value="ゴミ箱を空にしました。")

        with patch.dict(
            main.actions,
            {"empty_recycle_bin": main.Action(
                function,
                confirmation_message=action.confirmation_message,
            )},
        ):
            executed = main.execute_action(
                "empty_recycle_bin",
                {},
                input_function=lambda _: "n",
            )

        self.assertFalse(executed)
        function.assert_not_called()

    def test_empty_recycle_bin_runs_after_confirmation(self) -> None:
        call_tracker = Mock()

        def function() -> str:
            call_tracker()
            return "ゴミ箱を空にしました。"

        with patch.dict(
            main.actions,
            {"empty_recycle_bin": main.Action(
                function,
                confirmation_message="実行しますか？",
            )},
        ):
            executed = main.execute_action(
                "empty_recycle_bin",
                {"args": {}},
                input_function=lambda _: "y",
            )

        self.assertTrue(executed)
        call_tracker.assert_called_once_with()

    def test_action_with_arguments_rejects_unknown_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "未定義の引数"):
            main.execute_action(
                "google_search",
                {"query": "生成AI", "args": {}},
            )

    @patch("main.ctypes.windll.shell32.SHEmptyRecycleBinW", return_value=0)
    def test_empty_recycle_bin_calls_windows_api(self, empty: Mock) -> None:
        with patch("main.sys.platform", "win32"):
            self.assertEqual(main.empty_recycle_bin(), "ゴミ箱を空にしました。")

        empty.assert_called_once_with(None, None, 0x0001 | 0x0002 | 0x0004)


if __name__ == "__main__":
    unittest.main()
