import ctypes
import inspect
import json
import os
import platform
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

from ollama import chat


SettingsPage = Literal[
    "system",
    "display",
    "sound",
    "notifications",
    "network",
    "bluetooth",
    "apps",
    "default_apps",
    "storage",
    "power",
    "privacy",
    "windows_update",
]

SETTINGS_PAGES: dict[str, tuple[str, str]] = {
    "system": ("システム情報", "ms-settings:about"),
    "display": ("ディスプレイ", "ms-settings:display"),
    "sound": ("サウンド", "ms-settings:sound"),
    "notifications": ("通知", "ms-settings:notifications"),
    "network": ("ネットワークとインターネット", "ms-settings:network-status"),
    "bluetooth": ("Bluetoothとデバイス", "ms-settings:bluetooth"),
    "apps": ("インストールされているアプリ", "ms-settings:appsfeatures"),
    "default_apps": ("既定のアプリ", "ms-settings:defaultapps"),
    "storage": ("ストレージ", "ms-settings:storagesense"),
    "power": ("電源", "ms-settings:powersleep"),
    "privacy": ("プライバシーとセキュリティ", "ms-settings:privacy"),
    "windows_update": ("Windows Update", "ms-settings:windowsupdate"),
}

COPY_TEXT_SCRIPT = r"""
$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$text = [Console]::In.ReadToEnd()
[System.Windows.Forms.Clipboard]::SetText($text)
"""

GET_CLIPBOARD_TEXT_SCRIPT = r"""
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
if (-not [System.Windows.Forms.Clipboard]::ContainsText()) {
    throw "The clipboard does not contain text."
}
[Console]::Out.Write([System.Windows.Forms.Clipboard]::GetText())
"""

CURRENT_PAGE_SCRIPT = r"""
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

public static class BrowserWindowFinder
{
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassName(
        IntPtr hWnd,
        StringBuilder className,
        int maxCount
    );

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(
        IntPtr hWnd,
        out uint processId
    );

    public static IntPtr[] GetWindows()
    {
        var windows = new List<IntPtr>();

        EnumWindows(delegate (IntPtr hWnd, IntPtr lParam)
        {
            if (!IsWindowVisible(hWnd))
            {
                return true;
            }

            var className = new StringBuilder(256);
            GetClassName(hWnd, className, className.Capacity);
            if (!className.ToString().StartsWith("Chrome_WidgetWin"))
            {
                return true;
            }

            uint processId;
            GetWindowThreadProcessId(hWnd, out processId);

            try
            {
                string processName = Process.GetProcessById((int)processId)
                    .ProcessName.ToLowerInvariant();
                if (processName == "chrome" || processName == "msedge")
                {
                    windows.Add(hWnd);
                }
            }
            catch
            {
            }

            return true;
        }, IntPtr.Zero);

        return windows.ToArray();
    }
}
"@

$browser = $null
foreach ($handle in [BrowserWindowFinder]::GetWindows()) {
    try {
        $candidate = [System.Windows.Automation.AutomationElement]::FromHandle(
            $handle
        )
        if (-not [string]::IsNullOrWhiteSpace($candidate.Current.Name)) {
            $browser = $candidate
            break
        }
    } catch {
    }
}

if ($null -eq $browser) {
    throw "Chrome or Edge window was not found."
}

$editCondition = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Edit
)
$edits = $browser.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    $editCondition
)
$candidates = @()

foreach ($edit in $edits) {
    try {
        $name = $edit.Current.Name
        $valuePattern = $edit.GetCurrentPattern(
            [System.Windows.Automation.ValuePattern]::Pattern
        )
        $value = $valuePattern.Current.Value

        if ([string]::IsNullOrWhiteSpace($value)) {
            continue
        }

        $isUrl = (
            $value -match "^[a-zA-Z][a-zA-Z0-9+.-]*:" -or
            $value -match "^[^\s]+\.[^\s]+"
        )
        if (-not $isUrl) {
            continue
        }

        $score = 0
        if ($name -match "(?i)address|location|url|アドレス") {
            $score += 100
        }
        if ($value -match "^[a-zA-Z][a-zA-Z0-9+.-]*:") {
            $score += 50
        } else {
            $score += 10
        }

        if ($score -gt 0) {
            $candidates += [PSCustomObject]@{
                Value = $value
                Score = $score
            }
        }
    } catch {
    }
}

$address = $candidates |
    Sort-Object Score -Descending |
    Select-Object -First 1

if ($null -eq $address) {
    throw "Browser address bar was not found."
}

$title = $browser.Current.Name -replace (
    "\s+[-–]\s+(Google Chrome|Microsoft Edge)$"
), ""

[PSCustomObject]@{
    title = $title
    url = $address.Value
} | ConvertTo-Json -Compress
"""


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


def get_onedrive_directory() -> Path:
    """現在のユーザーのOneDrive内にあるアプリ用フォルダを返す。"""
    if sys.platform != "win32":
        raise OSError("OneDriveへの保存はWindowsでのみ利用できます。")

    for variable in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        value = os.environ.get(variable)
        if value and Path(value).is_dir():
            return Path(value) / "Local Actions"

    raise OSError(
        "OneDriveフォルダを特定できませんでした。"
        "OneDriveへサインインしてから再実行してください。"
    )


def get_saved_pages_path() -> Path:
    """保存したページを書き込むOneDrive上のファイルパスを返す。"""
    return get_onedrive_directory() / "saved_pages.md"


def get_notes_path() -> Path:
    """テキストメモを書き込むOneDrive上のファイルパスを返す。"""
    return get_onedrive_directory() / "notes.md"


def open_downloads_folder() -> str:
    """現在のユーザーのダウンロードフォルダをExplorerで開く。"""
    if sys.platform != "win32":
        raise OSError("ダウンロードフォルダを開く操作はWindowsでのみ利用できます。")

    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        raise OSError(f"ダウンロードフォルダが見つかりません: {downloads}")
    os.startfile(downloads)
    return f"ダウンロードフォルダを開きました。\n{downloads}"


def open_settings(page: SettingsPage) -> str:
    """許可済みのWindows設定ページを開く。

    Args:
        page: 開くページ。system、display、sound、notifications、network、
            bluetooth、apps、default_apps、storage、power、privacy、
            windows_updateのいずれか。
    """
    if sys.platform != "win32":
        raise OSError("Windows設定を開く操作はWindowsでのみ利用できます。")

    setting = SETTINGS_PAGES.get(page)
    if setting is None:
        allowed = ", ".join(SETTINGS_PAGES)
        raise ValueError(f"未対応の設定ページです: {page}（許可値: {allowed}）")

    label, uri = setting
    os.startfile(uri)
    return f"Windows設定の「{label}」を開きました。"


def run_clipboard_script(script: str, text: str | None = None) -> str:
    """固定PowerShellスクリプトでWindowsクリップボードを操作する。

    Args:
        script: Python側で定義した固定スクリプト。
        text: スクリプトへUTF-8で渡す標準入力。
    """
    if sys.platform != "win32":
        raise OSError("クリップボード操作はWindowsでのみ利用できます。")

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Sta",
            "-Command",
            script,
        ],
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        raise OSError(
            "クリップボードを操作できませんでした。"
            + (f"\n{detail}" if detail else "")
        )
    return completed.stdout


def copy_text(text: str) -> str:
    """指定されたテキストを改変せずクリップボードへコピーする。

    Args:
        text: コピーするテキスト。
    """
    if not text:
        raise ValueError("コピーするテキストが空です。")
    run_clipboard_script(COPY_TEXT_SCRIPT, text)
    return f"テキストをクリップボードへコピーしました（{len(text)}文字）。"


def get_clipboard_text() -> str:
    """クリップボードにあるテキストを取得する。"""
    text = run_clipboard_script(GET_CLIPBOARD_TEXT_SCRIPT)
    if not text:
        raise ValueError("クリップボードのテキストが空です。")
    return text


def format_text_note(text: str) -> str:
    """テキストをMarkdownのリスト項目へ変換する。

    Args:
        text: メモへ追記するテキスト。
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("メモするテキストが空です。")
    lines = normalized.splitlines()
    return "- " + "\n  ".join(lines) + "\n"


def create_text_note(text: str) -> str:
    """指定されたテキストをOneDrive上の固定メモへ追記する。

    Args:
        text: 日本語や改行を保持して追記する内容。
    """
    entry = format_text_note(text)
    notes_path = get_notes_path()
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    needs_heading = not notes_path.exists() or notes_path.stat().st_size == 0

    with notes_path.open("a", encoding="utf-8", newline="\n") as memo:
        if needs_heading:
            memo.write("# Notes\n\n")
        memo.write(entry)

    return f"メモを保存しました。\n{notes_path}"


def get_current_page() -> tuple[str, str]:
    """直近のChromeまたはEdgeウィンドウからタイトルとURLを取得する。"""
    if sys.platform != "win32":
        raise OSError("現在ページの取得はWindowsでのみ利用できます。")

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            CURRENT_PAGE_SCRIPT,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        raise OSError(
            "ChromeまたはEdgeのページ情報を取得できませんでした。"
            "ブラウザを開いてから再実行してください。"
            + (f"\n{detail}" if detail else "")
        )

    try:
        payload = json.loads(completed.stdout.strip())
        title = payload["title"].strip()
        url = payload["url"].strip()
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
    ) as error:
        raise OSError("ブラウザから不正なページ情報が返されました。") from error

    if not title or not url:
        raise OSError("ブラウザから空のページ情報が返されました。")
    return title, url


def format_saved_page(title: str, url: str) -> str:
    """ページ情報をMarkdownのリスト項目へ変換する。

    Args:
        title: 保存するページタイトル。
        url: 保存するページURL。
    """
    safe_title = (
        title.replace("\r", " ")
        .replace("\n", " ")
        .replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    safe_url = (
        url.replace("\r", "")
        .replace("\n", "")
        .replace("<", "%3C")
        .replace(">", "%3E")
    )
    return f"- [{safe_title}](<{safe_url}>)\n"


def save_current_page() -> str:
    """直近のChromeまたはEdgeのページをMarkdownメモへ保存する。"""
    title, url = get_current_page()
    saved_pages_path = get_saved_pages_path()
    saved_pages_path.parent.mkdir(parents=True, exist_ok=True)
    needs_heading = (
        not saved_pages_path.exists()
        or saved_pages_path.stat().st_size == 0
    )

    with saved_pages_path.open("a", encoding="utf-8", newline="\n") as memo:
        if needs_heading:
            memo.write("# Saved Pages\n\n")
        memo.write(format_saved_page(title, url))

    return f"ページを保存しました: {title}\n{saved_pages_path}"


def open_recycle_bin() -> str:
    """Windowsのゴミ箱を開いて内容を表示する。"""
    os.startfile("shell:RecycleBinFolder")
    return "ゴミ箱を開きました。"


def format_bytes(size: int) -> str:
    """バイト数を小数1桁のGiB表記へ変換する。

    Args:
        size: 変換するバイト数。
    """
    return f"{size / (1024 ** 3):.1f} GiB"


def show_system_info() -> str:
    """OSとシステムドライブの容量を取得して表示用テキストを返す。"""
    if sys.platform != "win32":
        raise OSError("システム情報の表示はWindowsでのみ利用できます。")

    release, version, _, _ = platform.win32_ver()
    edition = platform.win32_edition()
    build = sys.getwindowsversion().build
    system_drive = os.environ.get("SystemDrive", "C:")
    usage = shutil.disk_usage(system_drive + "\\")

    os_name = " ".join(part for part in ("Windows", edition, release) if part)
    return "\n".join(
        [
            f"OS: {os_name}",
            f"バージョン: {version or '不明'}（ビルド {build}）",
            f"コンピューター名: {platform.node() or '不明'}",
            f"システムドライブ: {system_drive}",
            f"総容量: {format_bytes(usage.total)}",
            f"使用量: {format_bytes(usage.used)}",
            f"空き容量: {format_bytes(usage.free)}",
        ]
    )


def lock_pc() -> str:
    """確認を挟まず現在のWindowsセッションをロックする。"""
    if sys.platform != "win32":
        raise OSError("PCのロックはWindowsでのみ利用できます。")

    if not ctypes.windll.user32.LockWorkStation():
        raise OSError("PCをロックできませんでした。")
    return "PCをロックしました。"


def empty_recycle_bin() -> str:
    """Windowsのゴミ箱にある項目を完全に削除する。"""
    if sys.platform != "win32":
        raise OSError("ゴミ箱を空にする操作はWindowsでのみ利用できます。")

    flags = 0x0001 | 0x0002 | 0x0004
    result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
    if result != 0:
        hresult = result & 0xFFFFFFFF
        raise OSError(f"ゴミ箱を空にできませんでした（HRESULT 0x{hresult:08X}）。")
    return "ゴミ箱を空にしました。"


@dataclass(frozen=True)
class Action:
    """登録済み操作の実行方法と安全設定を保持する。"""

    function: Callable[..., str | None]
    open_result_in_browser: bool = False
    confirmation_message: str | None = None


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
) -> bool:
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
        print("操作をキャンセルしました。")
        return False

    result = action.function(**arguments)
    if action.open_result_in_browser and result:
        browser_opener(result)
    elif result:
        print(result)
    else:
        print("この操作はまだ未実装です。")
    return True


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


def main() -> None:
    """自然文から登録済み操作を選択し、安全設定に従って実行する。"""
    request = " ".join(sys.argv[1:]).strip() or input("指示> ").strip()
    name, arguments = select_action(request)
    arguments = normalize_action_arguments(name, arguments)
    result = {"operation": name, **arguments}
    print(json.dumps(result, ensure_ascii=False, indent=2))

    execute_action(name, arguments)


if __name__ == "__main__":
    main()
