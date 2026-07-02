from typing import Any

from ollama import chat

from local_actions.registry import actions


def select_action(request: str) -> tuple[str, dict[str, Any]]:
    """自然文から登録済み操作と引数を選択する。

    Args:
        request: ユーザーが入力した日本語の依頼。
    """
    response = chat(
        model="qwen3:1.7b",
        messages=[
            {
                "role": "system",
                "content": (
                    "ユーザーの依頼に最適なツールを必ず1つ選んでください。"
                    "引数では依頼文の日本語を保持し、他言語へ翻訳しないでください。"
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
        tools=[action.function for action in actions.values()],
        think=False,
        options={"temperature": 0},
    )

    if not response.message.tool_calls:
        raise SystemExit("操作を判定できませんでした。")
    if len(response.message.tool_calls) != 1:
        raise SystemExit("単発実行では操作を1つだけ指定してください。")

    call = response.message.tool_calls[0]
    return call.function.name, call.function.arguments
