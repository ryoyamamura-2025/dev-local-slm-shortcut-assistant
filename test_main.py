import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from local_actions import action_log
from local_actions import actions as main
from local_actions import cli, direct_commands, registry, slm


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

    @patch("local_actions.actions.get_current_page")
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
            with patch(
                "local_actions.actions.get_saved_pages_path",
                return_value=memo_path,
            ):
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

    @patch("local_actions.actions.subprocess.run")
    def test_get_current_page_parses_ui_automation_result(
        self,
        run: Mock,
    ) -> None:
        run.return_value = Mock(
            returncode=0,
            stdout='{"title":"Example","url":"https://example.com"}',
            stderr="",
        )

        with patch("local_actions.actions.sys.platform", "win32"):
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

    @patch("local_actions.actions.subprocess.run")
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
            patch("local_actions.actions.sys.platform", "win32"),
            self.assertRaisesRegex(OSError, "ページ情報を取得できません"),
        ):
            main.get_current_page()


class OneDriveNoteTests(unittest.TestCase):
    def test_get_onedrive_directory_uses_consumer_folder(self) -> None:
        with TemporaryDirectory() as directory:
            with (
                patch("local_actions.actions.sys.platform", "win32"),
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
            patch("local_actions.actions.sys.platform", "win32"),
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
            with patch(
                "local_actions.actions.get_notes_path",
                return_value=notes_path,
            ):
                result = main.create_text_note("牛乳を買う")
                main.create_text_note("パンを買う")

            self.assertEqual(
                notes_path.read_text(encoding="utf-8"),
                "# Notes\n\n- 牛乳を買う\n- パンを買う\n",
            )

        self.assertIn(str(notes_path), result)


class CalendarTaskTests(unittest.TestCase):
    def test_format_calendar_ics_includes_escaped_body(self) -> None:
        ics = main.format_calendar_ics(
            "【タスク】田中さんに返信",
            "2026-07-01T14:00:00",
            "メモ; 重要, 明日",
            "uid@dev-slm-shortcut",
            "20260701T050000Z",
        )

        self.assertIn("SUMMARY:【タスク】田中さんに返信", ics)
        self.assertIn("DTSTART:20260701T140000", ics)
        self.assertIn("DTEND:20260701T141500", ics)
        self.assertIn(r"DESCRIPTION:メモ\; 重要\, 明日", ics)

    def test_format_calendar_ics_omits_empty_description(self) -> None:
        ics = main.format_calendar_ics(
            "【リマインダ】水を飲む",
            "2026-07-01T14:00:00",
            "",
            "uid@dev-slm-shortcut",
            "20260701T050000Z",
        )

        event = ics.split("BEGIN:VALARM")[0]
        self.assertNotIn("DESCRIPTION:", event)

    @patch("local_actions.actions.time.sleep")
    @patch("local_actions.actions.os.startfile")
    def test_create_calendar_task_opens_and_cleans_up(
        self,
        startfile: Mock,
        sleep: Mock,
    ) -> None:
        with patch("local_actions.actions.sys.platform", "win32"):
            result = main.create_calendar_task(
                "2026-07-01T14:00:00",
                "タスク",
                "田中さんに返信",
            )

        opened_path = Path(startfile.call_args.args[0])
        self.assertEqual(opened_path.suffix, ".ics")
        self.assertFalse(opened_path.exists())
        self.assertIn("【タスク】田中さんに返信", result)

    def test_create_calendar_task_rejects_empty_title(self) -> None:
        with (
            patch("local_actions.actions.sys.platform", "win32"),
            self.assertRaisesRegex(ValueError, "タスク名が空"),
        ):
            main.create_calendar_task("2026-07-01T14:00:00", "タスク", "  ")

    def test_create_calendar_task_rejects_unknown_entity_type(self) -> None:
        with (
            patch("local_actions.actions.sys.platform", "win32"),
            self.assertRaisesRegex(ValueError, "未対応の種別"),
        ):
            main.create_calendar_task(
                "2026-07-01T14:00:00",
                "予定",
                "田中さんに返信",
            )


class WindowsActionTests(unittest.TestCase):
    @patch("local_actions.actions.os.startfile")
    def test_open_folder_opens_downloads_folder(
        self,
        startfile: Mock,
    ) -> None:
        with TemporaryDirectory() as directory:
            home = Path(directory)
            downloads = home / "Downloads"
            downloads.mkdir()
            with (
                patch("local_actions.actions.sys.platform", "win32"),
                patch("local_actions.actions.Path.home", return_value=home),
            ):
                result = main.open_folder("downloads")

        startfile.assert_called_once_with(downloads)
        self.assertIn(str(downloads), result)

    @patch("local_actions.actions.os.startfile")
    def test_open_folder_opens_onedrive_save_directory(
        self,
        startfile: Mock,
    ) -> None:
        with TemporaryDirectory() as directory:
            onedrive = Path(directory)
            save_directory = onedrive / "Local Actions"
            save_directory.mkdir()
            with (
                patch("local_actions.actions.sys.platform", "win32"),
                patch.dict(
                    main.os.environ,
                    {"OneDriveConsumer": str(onedrive)},
                    clear=True,
                ),
            ):
                result = main.open_folder("onedrive")

        startfile.assert_called_once_with(save_directory)
        self.assertIn(str(save_directory), result)

    def test_open_folder_rejects_unknown_folder(self) -> None:
        with (
            patch("local_actions.actions.sys.platform", "win32"),
            self.assertRaisesRegex(ValueError, "未対応のフォルダ"),
        ):
            main.open_folder("arbitrary")  # type: ignore[arg-type]

    @patch("local_actions.actions.os.startfile")
    def test_open_settings_uses_allowlisted_uri(self, startfile: Mock) -> None:
        with patch("local_actions.actions.sys.platform", "win32"):
            result = main.open_settings("windows_update")

        startfile.assert_called_once_with("ms-settings:windowsupdate")
        self.assertIn("Windows Update", result)

    def test_open_settings_rejects_unknown_page(self) -> None:
        with (
            patch("local_actions.actions.sys.platform", "win32"),
            self.assertRaisesRegex(ValueError, "未対応の設定ページ"),
        ):
            main.open_settings("arbitrary")  # type: ignore[arg-type]

    @patch("local_actions.actions.run_clipboard_script")
    def test_copy_text_passes_text_without_modification(
        self,
        run_script: Mock,
    ) -> None:
        text = "日本語\nをそのまま"
        result = main.copy_text(text)

        run_script.assert_called_once_with(main.COPY_TEXT_SCRIPT, text)
        self.assertIn(f"{len(text)}文字", result)

    @patch(
        "local_actions.actions.run_clipboard_script",
        return_value="コピー済みの内容",
    )
    def test_get_clipboard_text_returns_script_output(
        self,
        run_script: Mock,
    ) -> None:
        self.assertEqual(main.get_clipboard_text(), "コピー済みの内容")
        run_script.assert_called_once_with(main.GET_CLIPBOARD_TEXT_SCRIPT)

    @patch("local_actions.actions.shutil.disk_usage")
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
            patch("local_actions.actions.sys.platform", "win32"),
            patch(
                "local_actions.actions.sys.getwindowsversion",
                return_value=windows_version,
            ),
            patch(
                "local_actions.actions.platform.win32_ver",
                return_value=("11", "10.0", "", ""),
            ),
            patch(
                "local_actions.actions.platform.win32_edition",
                return_value="Professional",
            ),
            patch(
                "local_actions.actions.platform.node",
                return_value="TEST-PC",
            ),
            patch.dict(main.os.environ, {"SystemDrive": "D:"}),
        ):
            result = main.show_system_info()

        disk_usage.assert_called_once_with("D:\\")
        self.assertIn("Windows Professional 11", result)
        self.assertIn("ビルド 26100", result)
        self.assertIn("空き容量: 60.0 GiB", result)

    @patch(
        "local_actions.actions.ctypes.windll.user32.LockWorkStation",
        return_value=1,
    )
    def test_lock_pc_runs_without_confirmation(self, lock: Mock) -> None:
        with patch("local_actions.actions.sys.platform", "win32"):
            self.assertEqual(main.lock_pc(), "PCをロックしました。")

        lock.assert_called_once_with()
        self.assertIsNone(registry.actions["lock_pc"].confirmation_message)


class SelectActionTests(unittest.TestCase):
    @patch("local_actions.slm.chat")
    def test_select_actions_preserves_structured_step_order(
        self,
        chat: Mock,
    ) -> None:
        chat.return_value = Mock(
            message=Mock(
                thinking="登録済みActionと引数を検討しました。",
                content=json.dumps(
                    {
                        "steps": [
                            {
                                "action": "get_clipboard_text",
                                "arguments": {},
                            },
                            {
                                "action": "create_calendar_task",
                                "arguments": {
                                    "start_time": "2026-07-03T15:00:00",
                                    "entity_type": "タスク",
                                    "title": "田中さんに返信",
                                },
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            ),
        )

        with patch("builtins.print") as output:
            self.assertEqual(
                slm.select_actions("クリップボードの内容と一緒に登録"),
                [
                    registry.PlannedAction("get_clipboard_text", {}),
                    registry.PlannedAction(
                        "create_calendar_task",
                        {
                            "start_time": "2026-07-03T15:00:00",
                            "entity_type": "タスク",
                            "title": "田中さんに返信",
                        },
                    ),
                ],
            )

        self.assertFalse(chat.call_args.kwargs["think"])
        output.assert_called_once_with("登録済みActionと引数を検討しました。")

    def test_plan_schema_exposes_body_as_optional_argument(self) -> None:
        schema = slm.build_action_plan_schema()
        step_schemas = schema["properties"]["steps"]["items"]["oneOf"]
        calendar_schema = next(
            step
            for step in step_schemas
            if step["properties"]["action"]["const"]
            == "create_calendar_task"
        )
        arguments_schema = calendar_schema["properties"]["arguments"]
        argument_properties = arguments_schema["properties"]

        self.assertIn("title", argument_properties)
        self.assertIn("body", argument_properties)
        self.assertNotIn("body", arguments_schema.get("required", []))


class CalendarScheduleTests(unittest.TestCase):
    def test_default_start_uses_lunch_slot_in_morning(self) -> None:
        self.assertEqual(
            slm.default_calendar_start(datetime(2026, 7, 3, 9, 0)),
            datetime(2026, 7, 3, 12, 15),
        )

    def test_default_start_uses_afternoon_slot_before_evening(self) -> None:
        self.assertEqual(
            slm.default_calendar_start(datetime(2026, 7, 3, 13, 30)),
            datetime(2026, 7, 3, 15, 0),
        )

    def test_default_start_moves_to_next_business_day_at_night(self) -> None:
        # 2026-07-03は金曜日のため、翌営業日は月曜の2026-07-06。
        self.assertEqual(
            slm.default_calendar_start(datetime(2026, 7, 3, 20, 0)),
            datetime(2026, 7, 6, 8, 45),
        )


class WorkflowTests(unittest.TestCase):
    def test_workflow_injects_clipboard_result_into_text_note(self) -> None:
        calls = Mock()
        note_input = registry.actions["create_text_note"].accepts_previous_as
        self.assertEqual(note_input, "text")

        def clipboard() -> str:
            return "クリップボードの実際の内容"

        def create_note(text: str) -> str:
            calls(text=text)
            return "メモを保存しました。"

        plan = [
            registry.PlannedAction("get_clipboard_text", {}),
            registry.PlannedAction("create_text_note", {}),
        ]

        with (
            patch.dict(
                registry.actions,
                {
                    "get_clipboard_text": registry.Action(clipboard),
                    "create_text_note": registry.Action(
                        create_note,
                        accepts_previous_as=note_input,
                    ),
                },
            ),
            patch("local_actions.registry.print"),
        ):
            registry.execute_workflow(plan)

        calls.assert_called_once_with(text="クリップボードの実際の内容")

    def test_workflow_injects_previous_result_into_registered_argument(
        self,
    ) -> None:
        calls = Mock()

        def clipboard() -> str:
            calls("clipboard")
            return "https://teams.example/message/123"

        def calendar(title: str, body: str = "") -> str:
            calls("calendar", title=title, body=body)
            return "[ダミー] 登録しました。"

        plan = [
            registry.PlannedAction("get_clipboard_text", {}),
            registry.PlannedAction(
                "create_calendar_task",
                {"title": "田中さんに返信"},
            ),
        ]

        with (
            patch.dict(
                registry.actions,
                {
                    "get_clipboard_text": registry.Action(clipboard),
                    "create_calendar_task": registry.Action(
                        calendar,
                        accepts_previous_as="body",
                    ),
                },
            ),
            patch("local_actions.registry.print") as print_function,
        ):
            result = registry.execute_workflow(plan)

        self.assertEqual(calls.call_args_list[0].args, ("clipboard",))
        self.assertEqual(calls.call_args_list[1].args, ("calendar",))
        self.assertEqual(
            calls.call_args_list[1].kwargs,
            {
                "title": "田中さんに返信",
                "body": "https://teams.example/message/123",
            },
        )
        print_function.assert_called_once_with("[ダミー] 登録しました。")
        self.assertEqual(result.result, "[ダミー] 登録しました。")

    def test_workflow_validates_all_steps_before_execution(self) -> None:
        clipboard = Mock(return_value="機密情報")
        plan = [
            registry.PlannedAction("get_clipboard_text", {}),
            registry.PlannedAction("google_search", {"query": "test"}),
        ]

        with (
            patch.dict(
                registry.actions,
                {"get_clipboard_text": registry.Action(clipboard)},
            ),
            self.assertRaisesRegex(ValueError, "受け取れません"),
        ):
            registry.execute_workflow(plan)

        clipboard.assert_not_called()

    def test_workflow_merges_model_body_with_previous_result(self) -> None:
        calls = Mock()

        def clipboard() -> str:
            return "https://teams.example/message/123"

        def calendar(title: str, body: str = "") -> str:
            calls(title=title, body=body)
            return "登録しました。"

        plan = [
            registry.PlannedAction("get_clipboard_text", {}),
            registry.PlannedAction(
                "create_calendar_task",
                {"title": "返信", "body": "モデルが書いた本文"},
            ),
        ]

        with (
            patch.dict(
                registry.actions,
                {
                    "get_clipboard_text": registry.Action(clipboard),
                    "create_calendar_task": registry.Action(
                        calendar,
                        accepts_previous_as="body",
                    ),
                },
            ),
            patch("local_actions.registry.print"),
        ):
            registry.execute_workflow(plan)

        self.assertEqual(
            calls.call_args.kwargs,
            {
                "title": "返信",
                "body": "モデルが書いた本文\n===\nhttps://teams.example/message/123",
            },
        )


class CommandLineOptionTests(unittest.TestCase):
    def test_format_action_list_contains_registered_actions(self) -> None:
        result = registry.format_action_list()

        self.assertIn(f"利用できる操作（{len(registry.actions)}件）", result)
        self.assertIn("google_search(query)", result)
        self.assertIn("open_chatgpt()", result)
        self.assertIn("open_folder(folder)", result)
        self.assertIn("Googleで検索する。", result)
        self.assertIn("empty_recycle_bin() [実行前に確認]", result)

    @patch("local_actions.cli.select_actions")
    @patch("local_actions.cli.print")
    def test_list_option_does_not_call_ollama(
        self,
        print_function: Mock,
        select_actions: Mock,
    ) -> None:
        with patch("local_actions.cli.sys.argv", ["main.py", "--list"]):
            cli.main()

        select_actions.assert_not_called()
        listing = print_function.call_args.args[0]
        self.assertIn(registry.format_action_list(), listing)
        self.assertIn("tmp削除", listing)
        self.assertIn("WSL圧縮", listing)


class DirectCommandTests(unittest.TestCase):
    def test_direct_command_aliases_use_exact_matching(self) -> None:
        self.assertEqual(
            direct_commands.match_direct_command("tmp削除").operation,
            "clear_temp_files",
        )
        self.assertEqual(
            direct_commands.match_direct_command("tmpdelete").operation,
            "clear_temp_files",
        )
        self.assertEqual(
            direct_commands.match_direct_command("WSL圧縮").operation,
            "prune_docker_and_compact_wsl",
        )
        self.assertEqual(
            direct_commands.match_direct_command("wslcomp").operation,
            "prune_docker_and_compact_wsl",
        )
        self.assertEqual(
            direct_commands.match_direct_command("ＷＳＬ圧縮"),
            None,
        )
        self.assertIsNone(
            direct_commands.match_direct_command("tmp削除して"),
        )

    @patch("local_actions.cli.select_actions")
    @patch("local_actions.cli.execute_direct_command")
    @patch("local_actions.cli.try_write_action_log")
    def test_direct_command_does_not_call_ollama(
        self,
        write_log: Mock,
        execute_direct: Mock,
        select_actions: Mock,
    ) -> None:
        execute_direct.return_value = "一時フォルダを掃除しました。"

        with (
            patch("local_actions.cli.sys.argv", ["main.py", "tmp削除"]),
            patch("local_actions.cli.print"),
        ):
            cli.main()

        select_actions.assert_not_called()
        execute_direct.assert_called_once()
        write_log.assert_called_once_with(
            "tmp削除",
            "clear_temp_files",
            {},
            "succeeded",
            result="一時フォルダを掃除しました。",
        )

    def test_clear_temp_files_skips_locked_entries(self) -> None:
        with TemporaryDirectory() as directory:
            temp_directory = Path(directory)
            removable = temp_directory / "remove.txt"
            locked = temp_directory / "locked.txt"
            removable.write_text("remove", encoding="utf-8")
            locked.write_text("locked", encoding="utf-8")
            original_unlink = Path.unlink

            def unlink(path: Path, *args: object, **kwargs: object) -> None:
                if path == locked:
                    raise PermissionError("使用中")
                original_unlink(path, *args, **kwargs)

            with (
                patch("local_actions.actions.sys.platform", "win32"),
                patch.dict("local_actions.actions.os.environ", {"TMP": directory}),
                patch("pathlib.Path.unlink", new=unlink),
            ):
                result = main.clear_temp_files()

        self.assertIn("削除: 1件", result)
        self.assertIn("スキップ: 1件", result)

    @patch("local_actions.actions.subprocess.run")
    def test_docker_prune_and_compact_wsl_use_fixed_commands(
        self,
        run: Mock,
    ) -> None:
        run.return_value = Mock(returncode=0, stdout="", stderr="")
        written_script = ""
        original_named_temp = main.tempfile.NamedTemporaryFile

        def named_temp(*args: object, **kwargs: object):
            nonlocal written_script
            temporary = original_named_temp(*args, **kwargs)
            original_write = temporary.write

            def write(value: str) -> int:
                nonlocal written_script
                written_script = value
                return original_write(value)

            temporary.write = write
            return temporary

        with (
            patch("local_actions.actions.sys.platform", "win32"),
            patch("pathlib.Path.is_file", return_value=True),
            patch(
                "local_actions.actions.tempfile.NamedTemporaryFile",
                side_effect=named_temp,
            ),
        ):
            result = main.prune_docker_and_compact_wsl()

        self.assertEqual(
            [call.args[0] for call in run.call_args_list[:3]],
            [
                [
                    "wsl.exe",
                    "--exec",
                    "docker",
                    "image",
                    "prune",
                    "--all",
                    "--force",
                ],
                [
                    "wsl.exe",
                    "--exec",
                    "docker",
                    "volume",
                    "prune",
                    "--force",
                ],
                [
                    "wsl.exe",
                    "--exec",
                    "docker",
                    "builder",
                    "prune",
                    "--force",
                ],
            ],
        )
        self.assertEqual(run.call_args_list[3].args[0], ["wsl.exe", "--shutdown"])
        self.assertIn(
            f'select vdisk file="{main.WSL_VIRTUAL_DISK_PATH}"',
            written_script,
        )
        self.assertIn("attach vdisk readonly", written_script)
        self.assertIn("compact vdisk", written_script)
        self.assertIn("detach vdisk", written_script)
        self.assertIn(str(main.WSL_VIRTUAL_DISK_PATH), result)


class ActionLogTests(unittest.TestCase):
    def test_write_action_log_appends_utf8_json_lines(self) -> None:
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "logs" / "actions.jsonl"
            with patch(
                "local_actions.action_log.get_action_log_path",
                return_value=log_path,
            ):
                action_log.write_action_log(
                    "大分駅の近くを検索して",
                    "google_search",
                    {"query": "大分駅の近く"},
                    "succeeded",
                    result="https://example.com/日本語",
                )
                action_log.write_action_log(
                    "キャンセルして",
                    "empty_recycle_bin",
                    {},
                    "cancelled",
                    result="操作をキャンセルしました。",
                )

            entries = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["request"], "大分駅の近くを検索して")
        self.assertEqual(entries[0]["arguments"], {"query": "大分駅の近く"})
        self.assertEqual(entries[0]["status"], "succeeded")
        self.assertIsNone(entries[0]["error"])
        self.assertEqual(entries[1]["status"], "cancelled")
        self.assertIn("T", entries[0]["timestamp"])

    def test_log_path_uses_onedrive_directory(self) -> None:
        onedrive_directory = Path(r"C:\Users\test\OneDrive\Local Actions")
        with patch(
            "local_actions.action_log.get_onedrive_directory",
            return_value=onedrive_directory,
        ):
            self.assertEqual(
                action_log.get_action_log_path(),
                onedrive_directory / "actions.jsonl",
            )

    @patch("local_actions.cli.execute_workflow")
    @patch("local_actions.cli.select_actions")
    @patch("local_actions.cli.try_write_action_log")
    def test_main_records_success(
        self,
        write_log: Mock,
        select_actions: Mock,
        execute_workflow: Mock,
    ) -> None:
        select_actions.return_value = [
            registry.PlannedAction("google_search", {"query": "生成AI"})
        ]
        execute_workflow.return_value = registry.ActionExecutionResult(
            "succeeded",
            "https://www.google.com/search?q=生成AI",
        )

        with patch("local_actions.cli.sys.argv", ["main.py", "生成AIを検索して"]):
            cli.main()

        write_log.assert_called_once_with(
            "生成AIを検索して",
            "google_search",
            {"query": "生成AI"},
            "succeeded",
            result="https://www.google.com/search?q=生成AI",
        )

    @patch(
        "local_actions.cli.select_actions",
        side_effect=RuntimeError("Ollama停止"),
    )
    @patch("local_actions.cli.try_write_action_log")
    def test_main_records_selection_failure(
        self,
        write_log: Mock,
        select_action: Mock,
    ) -> None:
        with (
            patch("local_actions.cli.sys.argv", ["main.py", "検索して"]),
            self.assertRaisesRegex(RuntimeError, "Ollama停止"),
        ):
            cli.main()

        write_log.assert_called_once_with(
            "検索して",
            None,
            {},
            "failed",
            error="RuntimeError: Ollama停止",
        )


class RecycleBinActionTests(unittest.TestCase):
    @patch("local_actions.actions.os.startfile")
    def test_open_recycle_bin_uses_shell_folder(self, startfile: Mock) -> None:
        self.assertEqual(main.open_recycle_bin(), "ゴミ箱を開きました。")
        startfile.assert_called_once_with("shell:RecycleBinFolder")

    def test_empty_recycle_bin_requires_confirmation(self) -> None:
        action = registry.actions["empty_recycle_bin"]
        function = Mock(return_value="ゴミ箱を空にしました。")

        with patch.dict(
            registry.actions,
            {"empty_recycle_bin": registry.Action(
                function,
                confirmation_message=action.confirmation_message,
            )},
        ):
            executed = registry.execute_action(
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
            registry.actions,
            {"empty_recycle_bin": registry.Action(
                function,
                confirmation_message="実行しますか？",
            )},
        ):
            executed = registry.execute_action(
                "empty_recycle_bin",
                {"args": {}},
                input_function=lambda _: "y",
            )

        self.assertTrue(executed)
        call_tracker.assert_called_once_with()

    def test_action_with_arguments_rejects_unknown_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "未定義の引数"):
            registry.execute_action(
                "google_search",
                {"query": "生成AI", "args": {}},
            )

    @patch(
        "local_actions.actions.ctypes.windll.shell32.SHEmptyRecycleBinW",
        return_value=0,
    )
    def test_empty_recycle_bin_calls_windows_api(self, empty: Mock) -> None:
        with patch("local_actions.actions.sys.platform", "win32"):
            self.assertEqual(main.empty_recycle_bin(), "ゴミ箱を空にしました。")

        empty.assert_called_once_with(None, None, 0x0001 | 0x0002 | 0x0004)


if __name__ == "__main__":
    unittest.main()
