import inspect
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from local_actions.actions import (
    copy_text,
    create_text_note,
    empty_recycle_bin,
    get_clipboard_text,
    google_maps_search,
    google_search,
    lock_pc,
    open_chatgpt,
    open_downloads_folder,
    open_settings,
    open_url,
    save_current_page,
    show_system_info,
    x_search,
)


@dataclass(frozen=True)
class Action:
    """登録済み操作の実行方法と安全設定を保持する。"""

    function: Callable[..., str | None]
    open_result_in_browser: bool = False
    confirmation_message: str | None = None


@dataclass(frozen=True)
class ActionExecutionResult:
    """登録済み操作の実行状態と戻り値を保持する。"""

    status: Literal["succeeded", "cancelled"]
    result: str | None

    def __bool__(self) -> bool:
        """操作を実行した場合だけTrueを返す。"""
        return self.status == "succeeded"


actions = {
    action.function.__name__: action
    for action in [
        Action(google_search, open_result_in_browser=True),
        Action(google_maps_search, open_result_in_browser=True),
        Action(x_search, open_result_in_browser=True),
        Action(open_url, open_result_in_browser=True),
        Action(open_chatgpt, open_result_in_browser=True),
        Action(open_downloads_folder),
        Action(open_settings),
        Action(copy_text),
        Action(create_text_note),
        Action(get_clipboard_text),
        Action(show_system_info),
        Action(lock_pc),
        Action(save_current_page),
        Action(
            empty_recycle_bin,
            confirmation_message=(
                "ゴミ箱内のすべての項目を完全に削除します。"
                "この操作は元に戻せません。実行しますか？"
            ),
        ),
    ]
}


def format_action_list() -> str:
    """登録済み操作の関数名、引数、説明を一覧表示用に整形する。"""
    lines = [f"利用できる操作（{len(actions)}件）:"]
    for name, action in actions.items():
        parameters = ", ".join(inspect.signature(action.function).parameters)
        description = (
            inspect.getdoc(action.function) or "説明はありません。"
        ).splitlines()[0]
        confirmation = " [実行前に確認]" if action.confirmation_message else ""
        lines.append(f"  {name}({parameters}){confirmation}")
        lines.append(f"    {description}")
    return "\n".join(lines)


def confirm_action(
    message: str,
    input_function: Callable[[str], str] = input,
) -> bool:
    """危険な操作を実行してよいかユーザーへ確認する。

    Args:
        message: 操作内容と影響を示す確認メッセージ。
        input_function: 確認入力に使う関数。
    """
    answer = input_function(f"{message} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def normalize_action_arguments(
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """モデルの引数を登録済み関数のシグネチャに合わせて検証する。

    Args:
        name: 実行する登録済みツール名。
        arguments: モデルが抽出したツール引数。
    """
    action = actions.get(name)
    if action is None:
        raise ValueError(f"未登録の操作です: {name}")

    parameters = inspect.signature(action.function).parameters
    if not parameters:
        return {}

    unexpected = set(arguments) - set(parameters)
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise ValueError(f"{name}に未定義の引数が指定されました: {names}")

    required = {
        parameter.name
        for parameter in parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    }
    missing = required - set(arguments)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"{name}に必要な引数がありません: {names}")

    return arguments


def execute_action(
    name: str,
    arguments: dict[str, Any],
    input_function: Callable[[str], str] = input,
    browser_opener: Callable[[str], Any] = webbrowser.open_new_tab,
) -> ActionExecutionResult:
    """許可リストの操作を安全設定に従って実行する。

    Args:
        name: 実行する登録済みツール名。
        arguments: モデルが抽出したツール引数。
        input_function: 危険操作の確認入力に使う関数。
        browser_opener: URLを開くための関数。
    """
    action = actions.get(name)
    if action is None:
        raise ValueError(f"未登録の操作です: {name}")
    arguments = normalize_action_arguments(name, arguments)

    if action.confirmation_message and not confirm_action(
        action.confirmation_message,
        input_function,
    ):
        result = "操作をキャンセルしました。"
        print(result)
        return ActionExecutionResult("cancelled", result)

    result = action.function(**arguments)
    if action.open_result_in_browser and result:
        browser_opener(result)
    elif result:
        print(result)
    else:
        print("この操作はまだ未実装です。")
    return ActionExecutionResult("succeeded", result)
