import inspect
import json
from typing import Any, Literal, get_args, get_origin

from ollama import chat

from local_actions.registry import PlannedAction, actions


def parameter_schema(annotation: Any) -> dict[str, Any]:
    """Pythonの引数型をSLMへ渡す最小限のJSON Schemaへ変換する。"""
    if get_origin(annotation) is Literal:
        values = list(get_args(annotation))
        return {"type": "string", "enum": values}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    return {"type": "string"}


def build_action_plan_schema() -> dict[str, Any]:
    """登録済みActionから最大3ステップの計画用JSON Schemaを作る。"""
    step_schemas = []
    for name, action in actions.items():
        properties: dict[str, Any] = {}
        required: list[str] = []
        for parameter in inspect.signature(action.function).parameters.values():
            if parameter.name == action.accepts_previous_as:
                continue
            properties[parameter.name] = parameter_schema(parameter.annotation)
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter.name)

        argument_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            argument_schema["required"] = required

        step_schemas.append(
            {
                "type": "object",
                "required": ["action", "arguments"],
                "additionalProperties": False,
                "properties": {
                    "action": {"const": name},
                    "arguments": argument_schema,
                },
            }
        )

    return {
        "type": "object",
        "required": ["steps"],
        "additionalProperties": False,
        "properties": {
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {"oneOf": step_schemas},
            }
        },
    }


def format_action_catalog() -> str:
    """登録済みActionの名前、SLM入力引数、説明を一覧化する。"""
    lines = ["利用できるAction:"]
    for name, action in actions.items():
        parameters = [
            parameter.name
            for parameter in inspect.signature(action.function).parameters.values()
            if parameter.name != action.accepts_previous_as
        ]
        description = (
            inspect.getdoc(action.function) or "説明はありません。"
        ).splitlines()[0]
        lines.append(f"- {name}({', '.join(parameters)}): {description}")
    return "\n".join(lines)


def select_actions(request: str) -> list[PlannedAction]:
    """自然文から最大3個の登録済み操作と引数を順番に選択する。

    Args:
        request: ユーザーが入力した日本語の依頼。
    """
    response = chat(
        model="qwen3:1.7b",
        messages=[
            {
                "role": "system",
                "content": (
                    format_action_catalog()
                    + "\n\n"
                    "ユーザーの依頼に必要なActionを実行順に1個以上3個以下で"
                    "選んでください。単独で完了する依頼には1個だけ選んでください。"
                    "引数では依頼文の日本語を保持し、他言語へ翻訳しないでください。"
                    "クリップボードの内容と一緒にカレンダータスクを登録する依頼では、"
                    "最初にget_clipboard_text、次にcreate_calendar_taskを選んでください。"
                    "create_calendar_taskのtitleにはタスク名だけを指定してください。"
                    "bodyはPythonが直前の結果から渡すため、計画の入力対象外です。"
                    "通常のカレンダータスク登録ではcreate_calendar_taskだけを選んでください。"
                    "「今のページを保存して」「このページをメモして」のように、"
                    "現在表示しているページを保存またはメモする依頼では"
                    "必ずsave_current_pageを選んでください。"
                    "指定された文章をメモする依頼はcreate_text_noteを選んでください。"
                    "文章をクリップボードへコピーする依頼はcopy_text、"
                    "クリップボードの内容を表示する依頼はget_clipboard_textを"
                    "選んでください。"
                    "「牛乳を買うとメモして」はcreate_text_noteを選び、"
                    "textには「牛乳を買う」を指定してください。"
                    "「クリップボードの内容を見せて」は"
                    "get_clipboard_textを選んでください。"
                    "「YouTubeを開いて」はopen_urlを選び、"
                    "urlにはhttps://www.youtube.com/を指定してください。"
                    "OneDriveの保存先を開く依頼はopen_folderを選び、"
                    "folderにはonedriveを指定してください。"
                    "ダウンロードフォルダを開く依頼はopen_folderを選び、"
                    "folderにはdownloadsを指定してください。"
                    "ゴミ箱の中身を完全に削除したい依頼はempty_recycle_binを選んでください。"
                ),
            },
            {"role": "user", "content": request},
        ],
        format=build_action_plan_schema(),
        think=False,
        options={"temperature": 0},
    )

    try:
        payload = json.loads(response.message.content)
        steps = payload["steps"]
        return [
            PlannedAction(
                name=step["action"],
                arguments=dict(step["arguments"]),
            )
            for step in steps
        ]
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
    ) as error:
        raise SystemExit("操作を判定できませんでした。") from error
