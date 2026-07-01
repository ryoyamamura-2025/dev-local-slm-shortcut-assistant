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
            with patch("main.get_saved_pages_path", return_value=memo_path):
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


class OneDriveNoteTests(unittest.TestCase):
    def test_get_onedrive_directory_uses_consumer_folder(self) -> None:
        with TemporaryDirectory() as directory:
            with (
                patch("main.sys.platform", "win32"),
                patch.dict(
                    main.os.environ,
                    {"OneDriveConsumer": directory},
                    clear=True,
                ),
            ):
                self.assertEqual(
                    main.get_onedrive_directory(),
                    Path(directory) / "Local Actions",
                )

    def test_get_onedrive_directory_reports_missing_configuration(self) -> None:
        with (
            patch("main.sys.platform", "win32"),
            patch.dict(main.os.environ, {}, clear=True),
            self.assertRaisesRegex(OSError, "OneDriveフォルダを特定"),
        ):
            main.get_onedrive_directory()

    def test_format_text_note_preserves_multiline_text(self) -> None:
        self.assertEqual(
            main.format_text_note("買い物\r\n牛乳\nパン"),
            "- 買い物\n  牛乳\n  パン\n",
        )

    def test_create_text_note_appends_utf8_markdown(self) -> None:
        with TemporaryDirectory() as directory:
            notes_path = Path(directory) / "Local Actions" / "notes.md"
            with patch("main.get_notes_path", return_value=notes_path):
                result = main.create_text_note("牛乳を買う")
                main.create_text_note("パンを買う")

            self.assertEqual(
                notes_path.read_text(encoding="utf-8"),
                "# Notes\n\n- 牛乳を買う\n- パンを買う\n",
            )

        self.assertIn(str(notes_path), result)


class WindowsActionTests(unittest.TestCase):
    @patch("main.os.startfile")
    def test_open_downloads_folder_opens_current_user_folder(
        self,
        startfile: Mock,
    ) -> None:
        with TemporaryDirectory() as directory:
            home = Path(directory)
            downloads = home / "Downloads"
            downloads.mkdir()
            with (
                patch("main.sys.platform", "win32"),
                patch("main.Path.home", return_value=home),
            ):
                result = main.open_downloads_folder()

        startfile.assert_called_once_with(downloads)
        self.assertIn(str(downloads), result)

    @patch("main.os.startfile")
    def test_open_settings_uses_allowlisted_uri(self, startfile: Mock) -> None:
        with patch("main.sys.platform", "win32"):
            result = main.open_settings("windows_update")

        startfile.assert_called_once_with("ms-settings:windowsupdate")
        self.assertIn("Windows Update", result)

    def test_open_settings_rejects_unknown_page(self) -> None:
        with (
            patch("main.sys.platform", "win32"),
            self.assertRaisesRegex(ValueError, "未対応の設定ページ"),
        ):
            main.open_settings("arbitrary")  # type: ignore[arg-type]

    @patch("main.run_clipboard_script")
    def test_copy_text_passes_text_without_modification(
        self,
        run_script: Mock,
    ) -> None:
        text = "日本語\nをそのまま"
        result = main.copy_text(text)

        run_script.assert_called_once_with(main.COPY_TEXT_SCRIPT, text)
        self.assertIn(f"{len(text)}文字", result)

    @patch("main.run_clipboard_script", return_value="コピー済みの内容")
    def test_get_clipboard_text_returns_script_output(
        self,
        run_script: Mock,
    ) -> None:
        self.assertEqual(main.get_clipboard_text(), "コピー済みの内容")
        run_script.assert_called_once_with(main.GET_CLIPBOARD_TEXT_SCRIPT)

    @patch("main.shutil.disk_usage")
    def test_show_system_info_formats_windows_and_disk_data(
        self,
        disk_usage: Mock,
    ) -> None:
        disk_usage.return_value = Mock(
            total=100 * 1024 ** 3,
            used=40 * 1024 ** 3,
            free=60 * 1024 ** 3,
        )
        windows_version = Mock(build=26100)

        with (
            patch("main.sys.platform", "win32"),
            patch("main.sys.getwindowsversion", return_value=windows_version),
            patch("main.platform.win32_ver", return_value=("11", "10.0", "", "")),
            patch("main.platform.win32_edition", return_value="Professional"),
            patch("main.platform.node", return_value="TEST-PC"),
            patch.dict(main.os.environ, {"SystemDrive": "D:"}),
        ):
            result = main.show_system_info()

        disk_usage.assert_called_once_with("D:\\")
        self.assertIn("Windows Professional 11", result)
        self.assertIn("ビルド 26100", result)
        self.assertIn("空き容量: 60.0 GiB", result)

    @patch("main.ctypes.windll.user32.LockWorkStation", return_value=1)
    def test_lock_pc_runs_without_confirmation(self, lock: Mock) -> None:
        with patch("main.sys.platform", "win32"):
            self.assertEqual(main.lock_pc(), "PCをロックしました。")

        lock.assert_called_once_with()
        self.assertIsNone(main.actions["lock_pc"].confirmation_message)


class SelectActionTests(unittest.TestCase):
    @patch("main.chat")
    def test_single_action_mode_rejects_multiple_tool_calls(
        self,
        chat: Mock,
    ) -> None:
        chat.return_value = Mock(
            message=Mock(tool_calls=[Mock(), Mock()]),
        )

        with self.assertRaisesRegex(SystemExit, "操作を1つだけ"):
            main.select_action("複数の操作")


class CommandLineOptionTests(unittest.TestCase):
    def test_format_action_list_contains_registered_actions(self) -> None:
        result = main.format_action_list()

        self.assertIn(f"利用できる操作（{len(main.actions)}件）", result)
        self.assertIn("google_search(query)", result)
        self.assertIn("open_chatgpt()", result)
        self.assertIn("Googleで検索する。", result)
        self.assertIn("empty_recycle_bin() [実行前に確認]", result)

    @patch("main.select_action")
    @patch("main.print")
    def test_list_option_does_not_call_ollama(
        self,
        print_function: Mock,
        select_action: Mock,
    ) -> None:
        with patch("main.sys.argv", ["main.py", "--list"]):
            main.main()

        select_action.assert_not_called()
        print_function.assert_called_once_with(main.format_action_list())


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
