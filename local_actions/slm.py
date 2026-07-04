import inspect
import json
from datetime import date, datetime, timedelta
from typing import Any, Literal, get_args, get_origin

from ollama import chat

from local_actions.registry import PlannedAction, actions


WEEKDAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


def next_business_day(from_date: date) -> date:
    """指定日の翌営業日（土日を除く）を返す。

    Args:
        from_date: 起点となる日付。
    """
    next_day = from_date + timedelta(days=1)
    while next_day.weekday() >= 5:  # 5=土, 6=日
        next_day += timedelta(days=1)
    return next_day


def default_calendar_start(now: datetime) -> datetime:
    """開始時刻が未指定の予定に使う既定の開始日時を返す。

    Args:
        now: 現在日時。
    """
    if now.hour < 12:
        return now.replace(hour=12, minute=15, second=0, microsecond=0)
    if now.hour < 18:
        return now.replace(hour=15, minute=0, second=0, microsecond=0)
    next_day = next_business_day(now.date())
    return datetime(next_day.year, next_day.month, next_day.day, 8, 45)


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
        parameters = list(inspect.signature(action.function).parameters)
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
    now = datetime.now()
    default_start = default_calendar_start(now)

    response = chat(
        model="qwen3:1.7b",
        messages=[
            {
                "role": "system",
                "content": (
                    f"現在日時: {now.strftime('%Y-%m-%d %H:%M')}"
                    f"（{WEEKDAY_NAMES[now.weekday()]}曜日）\n"
                    + format_action_catalog()
                    + "\n\n"
                    "ユーザーの依頼に必要なActionを実行順に1個以上3個以下で"
                    "選んでください。単独で完了する依頼には1個だけ選んでください。"
                    "引数では依頼文の日本語を保持し、他言語へ翻訳しないでください。"
                    "予定・タスク・リマインダの登録・追加・作成を求める依頼では"
                    "create_calendar_taskを選んでください。"
                    "entity_typeはタスクまたはリマインダから選び、"
                    "titleには予定名だけを指定してください。"
                    "start_timeはISO 8601形式（例: 2026-07-01T14:00:00）で指定し、"
                    "開始時刻が明示されていない場合は"
                    f"{default_start.strftime('%Y-%m-%dT%H:%M:00')}を使用してください。"
                    "依頼文に本文が示されていればcreate_calendar_taskのbodyへ入れ、"
                    "示されていなければbodyは空にしてください。"
                    "メモやログとして保存する依頼ではcaptureを選んでください。"
                    "kindはメモならmemo、作業記録やログならlogを指定し、"
                    "titleには簡潔な題名を指定してください。"
                    "依頼文に本文があればcaptureのbodyへ入れてください。"
                    "クリップボードの内容を本文にする依頼では、最初に"
                    "get_clipboard_text、次にcaptureまたはcreate_calendar_taskを"
                    "選び、受け側のbodyは空のままにしてください"
                    "（Pythonが直前の結果を本文へ結合します）。"
                    "通常のカレンダー登録ではcreate_calendar_taskだけを選んでください。"
                    "Xの投稿検索、ホーム、プロフィール、最近のいいねを開く依頼では"
                    "x_openを選んでください。destinationはそれぞれsearch、home、"
                    "profile、likesを指定してください。searchではqueryへ検索語を"
                    "指定し、home、profile、likesではqueryを空にしてください。"
                    "profileとlikesのユーザー名はPythonが環境変数から注入します。"
                    "「今のページを保存して」「このページをメモして」のように、"
                    "現在表示しているページを保存またはメモする依頼では"
                    "get_current_page、captureの順に選んでください。"
                    "現在ページを予定の本文にする場合はget_current_page、"
                    "create_calendar_taskの順に選んでください。"
                    "クリップボードと現在ページの両方を保存または予定の本文に"
                    "する場合はget_clipboard_text、get_current_page、"
                    "captureまたはcreate_calendar_taskの順に選んでください。"
                    "これらの受け側のbodyは空のままにしてください。"
                    "文章をクリップボードへコピーする依頼はcopy_text、"
                    "クリップボードの内容を表示する依頼はget_clipboard_textを"
                    "選んでください。"
                    "「クリップボードの内容を見せて」は"
                    "get_clipboard_textを選んでください。"
                    "OneDriveの保存先を開く依頼はopen_folderを選び、"
                    "folderにはonedriveを指定してください。"
                    "ダウンロードフォルダを開く依頼はopen_folderを選び、"
                    "folderにはdownloadsを指定してください。"
                ),
            },
            {"role": "user", "content": request},
        ],
        format=build_action_plan_schema(),
        think=False,
        options={"temperature": 0},
    )

    if response.message.thinking:
        print(response.message.thinking)

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
