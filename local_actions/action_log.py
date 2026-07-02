import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from local_actions.actions import get_onedrive_directory


LogStatus = Literal["succeeded", "cancelled", "failed"]


def get_action_log_path() -> Path:
    """actの実行ログを書き込むOneDrive上のファイルパスを返す。"""
    override = os.environ.get("LOCAL_ACTIONS_LOG_PATH")
    if override:
        return Path(override)

    return get_onedrive_directory() / "actions.jsonl"


def write_action_log(
    request: str,
    operation: str | None,
    arguments: dict[str, Any],
    status: LogStatus,
    result: str | None = None,
    error: str | None = None,
) -> None:
    """actの入力、選択内容、実行結果をJSON Lines形式で追記する。

    Args:
        request: ユーザーが入力した自然文。
        operation: 選択された登録済みツール名。選択失敗時はNone。
        arguments: モデルが抽出したツール引数。
        status: succeeded、cancelled、failedのいずれか。
        result: 操作が返した値またはキャンセル結果。
        error: 失敗時の例外名とメッセージ。
    """
    log_path = get_action_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "request": request,
        "operation": operation,
        "arguments": arguments,
        "status": status,
        "result": result,
        "error": error,
    }
    with log_path.open("a", encoding="utf-8", newline="\n") as log_file:
        log_file.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def try_write_action_log(
    request: str,
    operation: str | None,
    arguments: dict[str, Any],
    status: LogStatus,
    result: str | None = None,
    error: str | None = None,
) -> None:
    """ログ保存の失敗で本来の操作結果を隠さず、標準エラーへ警告する。"""
    try:
        write_action_log(
            request,
            operation,
            arguments,
            status,
            result,
            error,
        )
    except (OSError, TypeError, ValueError) as log_error:
        print(f"警告: 実行ログを保存できませんでした: {log_error}", file=sys.stderr)
