import inspect
import json
import re
import sys
import time
from datetime import datetime
from typing import Any

from ollama import chat

from local_actions.registry import actions, execute_action
from local_actions.slm import WEEKDAY_NAMES, parameter_schema


MODEL = "qwen3.5:0.8b"
DEFAULT_MAX_TURNS = 5
PREVIEW_LIMIT = 300


def _parse_param_docs(docstring: str) -> dict[str, str]:
    """docstring の Args: セクションからパラメータ説明を抽出する。"""
    result: dict[str, str] = {}
    in_args = False
    current: str | None = None
    for line in docstring.splitlines():
        if line.strip() == "Args:":
            in_args = True
            continue
        if not in_args:
            continue
        if line.strip() and not line.startswith(" "):
            break
        match = re.match(r"    (\w+): (.+)", line)
        if match:
            current = match.group(1)
            result[current] = match.group(2)
        elif current and line.strip():
            result[current] += " " + line.strip()
    return result


def _build_tools() -> list[dict[str, Any]]:
    """登録済み actions から Ollama tools 形式のリストを構築する。"""
    tools = []
    for name, action in actions.items():
        sig = inspect.signature(action.function)
        docstring = inspect.getdoc(action.function) or ""
        description = docstring.splitlines()[0] if docstring else ""
        param_docs = _parse_param_docs(docstring)

        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in sig.parameters.items():
            schema: dict[str, Any] = dict(parameter_schema(param.annotation))
            if param_name in param_docs:
                schema["description"] = param_docs[param_name]
            properties[param_name] = schema
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        })
    return tools


def run_agent_loop(
    request: str,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> str | None:
    """Agent Loop でユーザーの依頼を処理する。

    Args:
        request: ユーザーが入力した日本語の依頼。
        max_turns: ツール呼び出しの最大ターン数。
    """
    now = datetime.now()
    tools = _build_tools()

    messages: list[Any] = [
        {
            "role": "system",
            "content": (
                f"現在日時: {now.strftime('%Y-%m-%d %H:%M')}"
                f"（{WEEKDAY_NAMES[now.weekday()]}曜日）\n"
                "ユーザーの依頼を達成するために利用できるツールを使ってください。"
            ),
        },
        {"role": "user", "content": request},
    ]

    last_result: str | None = None
    result_buffer: str | None = None
    total_start = time.perf_counter()

    while True:
        for turn in range(1, max_turns + 1):
            turn_start = time.perf_counter()
            # print(f"\n--- ターン {turn}/{max_turns} ---")

            response = chat(
                model=MODEL,
                messages=messages,
                tools=tools,
                think=False,
                options={"temperature": 0},
            )

            elapsed = time.perf_counter() - turn_start
            assistant_message = response.message
            messages.append(assistant_message)

            if not assistant_message.tool_calls:
                print(f"[{elapsed:.2f}s] ツール呼び出しなし")
                if assistant_message.content:
                    print(f"応答: {assistant_message.content}")
                break

            for tool_call in assistant_message.tool_calls:
                name = tool_call.function.name
                args = dict(tool_call.function.arguments)

                # accepts_previous_as パラメータにバッファを注入する
                action = actions.get(name)
                if action and action.accepts_previous_as and result_buffer is not None:
                    param = action.accepts_previous_as
                    model_value = args.get(param) or ""
                    if model_value:
                        args[param] = f"{model_value}\n===\n{result_buffer}"
                    else:
                        args[param] = result_buffer

                print(f"[{elapsed:.2f}s] {name}({json.dumps(args, ensure_ascii=False)})")

                try:
                    execution = execute_action(name, args, display_result=False)
                    tool_result = execution.result or "（結果なし）"
                    last_result = execution.result
                    if execution.result:
                        result_buffer = execution.result
                except Exception as exc:
                    tool_result = f"エラー: {type(exc).__name__}: {exc}"

                preview = str(tool_result)
                print(f"  → {preview[:120]}{'...' if len(preview) > 120 else ''}")

                model_content = str(tool_result)
                if len(model_content) > PREVIEW_LIMIT:
                    model_content = (
                        model_content[:PREVIEW_LIMIT]
                        + f"...（以降省略、計{len(model_content)}文字）"
                    )
                messages.append({
                    "role": "tool",
                    "content": model_content,
                    "tool_name": name,
                })
        else:
            print(f"\n最大ターン数（{max_turns}）に達しました。")

        try:
            next_input = input("\n指示> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not next_input:
            break
        messages.append({"role": "user", "content": next_input})

    total_elapsed = time.perf_counter() - total_start
    print(f"\n合計時間: {total_elapsed:.2f}s")
    return last_result


if __name__ == "__main__":
    _request = " ".join(sys.argv[1:]).strip() or input("指示> ").strip()
    run_agent_loop(_request)
