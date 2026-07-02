import json
import sys
from typing import Any

from local_actions.action_log import try_write_action_log
from local_actions.registry import (
    execute_action,
    format_action_list,
    normalize_action_arguments,
)
from local_actions.slm import select_action


def main() -> None:
    """自然文から登録済み操作を選択し、安全設定に従って実行する。"""
    arguments = sys.argv[1:]
    if len(arguments) == 1 and arguments[0] in {"--list", "-l"}:
        print(format_action_list())
        return

    request = " ".join(arguments).strip() or input("指示> ").strip()
    name: str | None = None
    action_arguments: dict[str, Any] = {}
    try:
        name, action_arguments = select_action(request)
        action_arguments = normalize_action_arguments(name, action_arguments)
        selection = {"operation": name, **action_arguments}
        print(json.dumps(selection, ensure_ascii=False, indent=2))

        execution = execute_action(name, action_arguments)
        try_write_action_log(
            request,
            name,
            action_arguments,
            execution.status,
            result=execution.result,
        )
    except (Exception, SystemExit) as error:
        try_write_action_log(
            request,
            name,
            action_arguments,
            "failed",
            error=f"{type(error).__name__}: {error}",
        )
        raise
