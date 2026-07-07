from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from local_actions.actions import (
    SETTINGS_PAGES,
    clear_temp_files,
    empty_recycle_bin,
    open_settings,
    prune_docker_and_compact_wsl,
    show_system_info,
)


@dataclass(frozen=True)
class DirectCommand:
    """SLMを介さず完全一致の短文で実行する固定操作を保持する。"""

    operation: str
    function: Callable[..., str]
    arguments: dict[str, Any] | None = None
    confirmation_message: str | None = None


_CLEAR_TEMP = DirectCommand("clear_temp_files", clear_temp_files)
_COMPACT_WSL = DirectCommand(
    "prune_docker_and_compact_wsl",
    prune_docker_and_compact_wsl,
)
_SYSTEM_INFO = DirectCommand("show_system_info", show_system_info)
_EMPTY_RECYCLE_BIN = DirectCommand(
    "empty_recycle_bin",
    empty_recycle_bin,
    confirmation_message=(
        "ゴミ箱内のすべての項目を完全に削除します。"
        "この操作は元に戻せません。実行しますか？"
    ),
)

DIRECT_COMMANDS: dict[str, DirectCommand] = {
    phrase.casefold(): command
    for command, phrases in (
        (
            _CLEAR_TEMP,
            (
                "tmpdelete",
                "tmp削除",
                "一時ファイル削除",
                "一時ファイルを削除",
            ),
        ),
        (
            _COMPACT_WSL,
            (
                "wslcomp",
                "wsl圧縮",
                "wslディスク圧縮",
                "wslのディスクを圧縮",
            ),
        ),
        (
            _SYSTEM_INFO,
            (
                "sysinfo",
                "システム情報",
            ),
        ),
        (
            _EMPTY_RECYCLE_BIN,
            (
                "emptytrash",
                "ゴミ箱を空にする",
            ),
        ),
    )
    for phrase in phrases
}


def match_direct_command(request: str) -> DirectCommand | None:
    """固定の短文と完全一致した直接実行コマンドを返す。

    Args:
        request: actへ渡された短文。
    """
    normalized = request.strip()
    command = DIRECT_COMMANDS.get(normalized.casefold())
    if command is not None:
        return command

    parts = normalized.split()
    if not parts or parts[0].casefold() not in {"settings", "設定"}:
        return None
    if len(parts) != 2:
        raise ValueError("settingsには設定ページを1つ指定してください。")

    page = parts[1].casefold()
    if page not in SETTINGS_PAGES:
        allowed = ", ".join(SETTINGS_PAGES)
        raise ValueError(
            f"未対応の設定ページです: {parts[1]}（許可値: {allowed}）"
        )
    return DirectCommand(
        "open_settings",
        open_settings,
        arguments={"page": page},
    )


def format_direct_command_list() -> str:
    """SLMを使わない固定コマンドを一覧表示用に整形する。"""
    return "\n".join(
        [
            "固定短文コマンド（SLM不使用）:",
            "  tmpdelete / tmp削除",
            "    %TMP%直下の削除可能な項目を削除する。",
            "  wslcomp / WSL圧縮",
            "    Dockerの不要データを削除してWSL仮想ディスクを圧縮する。",
            "  settings <page> / 設定 <page>",
            f"    Windows設定を開く。page: {', '.join(SETTINGS_PAGES)}",
            "  sysinfo / システム情報",
            "    OSとシステムドライブの情報を表示する。",
            "  emptytrash / ゴミ箱を空にする [実行前に確認]",
            "    ゴミ箱内のすべての項目を完全に削除する。",
        ]
    )


def execute_direct_command(
    command: DirectCommand,
    input_function: Callable[[str], str] = input,
) -> str:
    """固定コマンドを実行し、結果を表示して返す。

    Args:
        command: 完全一致で選択済みの固定コマンド。
    """
    if command.confirmation_message:
        answer = input_function(
            f"{command.confirmation_message} [y/N] "
        ).strip().lower()
        if answer not in {"y", "yes"}:
            result = "操作をキャンセルしました。"
            print(result)
            return result

    result = command.function(**(command.arguments or {}))
    print(result)
    return result