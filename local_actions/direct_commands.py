from collections.abc import Callable
from dataclasses import dataclass

from local_actions.actions import clear_temp_files, prune_docker_and_compact_wsl


@dataclass(frozen=True)
class DirectCommand:
    """SLMを介さず完全一致の短文で実行する固定操作を保持する。"""

    operation: str
    function: Callable[[], str]


_CLEAR_TEMP = DirectCommand("clear_temp_files", clear_temp_files)
_COMPACT_WSL = DirectCommand(
    "prune_docker_and_compact_wsl",
    prune_docker_and_compact_wsl,
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
    )
    for phrase in phrases
}


def match_direct_command(request: str) -> DirectCommand | None:
    """固定の短文と完全一致した直接実行コマンドを返す。

    Args:
        request: actへ渡された短文。
    """
    return DIRECT_COMMANDS.get(request.strip().casefold())


def format_direct_command_list() -> str:
    """SLMを使わない固定コマンドを一覧表示用に整形する。"""
    return "\n".join(
        [
            "固定短文コマンド（SLM不使用）:",
            "  tmpdelete / tmp削除",
            "    %TMP%直下の削除可能な項目を削除する。",
            "  wslcomp / WSL圧縮",
            "    Dockerの不要データを削除してWSL仮想ディスクを圧縮する。",
        ]
    )


def execute_direct_command(command: DirectCommand) -> str:
    """固定コマンドを実行し、結果を表示して返す。

    Args:
        command: 完全一致で選択済みの固定コマンド。
    """
    result = command.function()
    print(result)
    return result
