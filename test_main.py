import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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


class SaveCurrentPageTests(unittest.TestCase):
    def test_format_saved_page_escapes_markdown(self) -> None:
        self.assertEqual(
            main.format_saved_page(
                r"Example [page]",
                "https://example.com/a>b",
            ),
            r"- [Example \[page\]](<https://example.com/a%3Eb>)" + "\n",
        )

    @patch("main.get_current_page")
    def test_save_current_page_appends_utf8_markdown(
        self,
        get_current_page: Mock,
    ) -> None:
        get_current_page.return_value = (
            "日本語のページ",
            "https://example.com/日本語",
        )

        with TemporaryDirectory() as directory:
            memo_path = Path(directory) / "saved_pages.md"
            with patch("main.SAVED_PAGES_PATH", memo_path):
                result = main.save_current_page()
                main.save_current_page()

            self.assertEqual(
                memo_path.read_text(encoding="utf-8"),
                (
                    "# Saved Pages\n\n"
                    "- [日本語のページ](<https://example.com/日本語>)\n"
                    "- [日本語のページ](<https://example.com/日本語>)\n"
                ),
            )

        self.assertIn("ページを保存しました", result)

    @patch("main.subprocess.run")
    def test_get_current_page_parses_ui_automation_result(
        self,
        run: Mock,
    ) -> None:
        run.return_value = Mock(
            returncode=0,
            stdout='{"title":"Example","url":"https://example.com"}',
            stderr="",
        )

        with patch("main.sys.platform", "win32"):
            self.assertEqual(
                main.get_current_page(),
                ("Example", "https://example.com"),
            )

        command = run.call_args.args[0]
        self.assertEqual(command[:4], [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
        ])

    @patch("main.subprocess.run")
    def test_get_current_page_reports_ui_automation_failure(
        self,
        run: Mock,
    ) -> None:
        run.return_value = Mock(
            returncode=1,
            stdout="",
            stderr="Browser address bar was not found.",
        )

        with (
            patch("main.sys.platform", "win32"),
            self.assertRaisesRegex(OSError, "ページ情報を取得できません"),
        ):
            main.get_current_page()


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
