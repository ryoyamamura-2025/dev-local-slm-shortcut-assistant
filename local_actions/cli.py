import json
import sys

from local_actions.action_log import try_write_action_log
from local_actions.actions import run_pending_cleanup
from local_actions.direct_commands import (
    execute_direct_command,
    format_direct_command_list,
    match_direct_command,
)
from local_actions.registry import (
    execute_workflow,
    format_action_list,
)
from local_actions.slm import select_actions


def main() -> None:
    """自然文から登録済み操作を選択し、安全設定に従って実行する。"""
    arguments = sys.argv[1:]
    if len(arguments) == 1 and arguments[0] in {"--list", "-l"}:
        print(f"{format_action_list()}\n\n{format_direct_command_list()}")
        return

    run_pending_cleanup()
    request = " ".join(arguments).strip() or input("指示> ").strip()
    operation: str | None = None
    log_arguments: dict[str, object] = {}
    try:
        direct_command = match_direct_command(request)
        if direct_command is not None:
            operation = direct_command.operation
            direct_arguments = direct_command.arguments or {}
            print(
                json.dumps(
                    {
                        "operation": operation,
                        **direct_arguments,
                        "direct": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            result = execute_direct_command(direct_command)
            status = (
                "cancelled"
                if result == "操作をキャンセルしました。"
                else "succeeded"
            )
            try_write_action_log(
                request,
                operation,
                direct_arguments,
                status,
                result=result,
            )
            return

        plan = select_actions(request)
        selection = {
            "steps": [
                {"operation": step.name, **step.arguments}
                for step in plan
            ]
        }
        print(json.dumps(selection, ensure_ascii=False, indent=2))

        if len(plan) == 1:
            operation = plan[0].name
            log_arguments = dict(plan[0].arguments)
        else:
            operation = "workflow"
            log_arguments = {"steps": selection["steps"]}
        execution = execute_workflow(plan)
        try_write_action_log(
            request,
            operation,
            log_arguments,
            execution.status,
            result=execution.result,
        )
    except (Exception, SystemExit) as error:
        try_write_action_log(
            request,
            operation,
            log_arguments,
            "failed",
            error=f"{type(error).__name__}: {error}",
        )
        raise