import json
import sys
import webbrowser
from urllib.parse import urlencode

from ollama import chat


def google_search(query: str) -> str:
    """Googleで検索する。

    Args:
        query: 日本語を保持した検索語。
    """
    return "https://www.google.com/search?" + urlencode({"q": query})


def google_maps_search(query: str) -> str:
    """Googleマップで場所や店舗を検索する。

    Args:
        query: 日本語を保持した検索語。
    """
    return "https://www.google.com/maps/search/?" + urlencode(
        {"api": "1", "query": query}
    )


def x_search(query: str) -> str:
    """Xで投稿を検索する。

    Args:
        query: 日本語を保持した検索語。
    """
    return "https://x.com/search?" + urlencode(
        {"q": query, "src": "typed_query"}
    )


def open_url(url: str) -> str:
    """指定されたウェブサイトを開く。

    Args:
        url: 開くURL。
    """
    if not url.startswith(("https://", "http://")):
        url = "https://" + url
    return url


def open_chatgpt() -> str:
    """ChatGPTの新規チャットを開く。"""
    return "https://chatgpt.com/"


def save_current_page() -> None:
    """Chromeで表示中のページをメモへ保存する。"""
    return None


actions = {
    function.__name__: function
    for function in [
        google_search,
        google_maps_search,
        x_search,
        open_url,
        open_chatgpt,
        save_current_page,
    ]
}

request = " ".join(sys.argv[1:]).strip() or input("指示> ").strip()

response = chat(
    model="qwen3:1.7b",
    messages=[
        {
            "role": "system",
            "content": (
                "ユーザーの依頼に最適なツールを必ず1つ選んでください。"
                "引数では依頼文の日本語を保持し、他言語へ翻訳しないでください。"
            ),
        },
        {"role": "user", "content": request},
    ],
    tools=list(actions.values()),
    think=False,
    options={"temperature": 0},
)

if not response.message.tool_calls:
    raise SystemExit("操作を判定できませんでした。")

call = response.message.tool_calls[0]
arguments = call.function.arguments
result = {"operation": call.function.name, **arguments}
print(json.dumps(result, ensure_ascii=False, indent=2))

url = actions[call.function.name](**arguments)
if url:
    webbrowser.open_new_tab(url)
else:
    print("この操作はまだ未実装です。")