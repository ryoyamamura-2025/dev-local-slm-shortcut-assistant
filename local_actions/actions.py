import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, get_args
from urllib.parse import urlencode


CLEANUP_LIST_PATH = Path(__file__).with_name("pending_cleanup.txt")

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

FolderName = Literal["onedrive", "downloads"]

CalendarEntityType = Literal["タスク", "リマインダ"]

CALENDAR_ENTITY_TYPES: tuple[str, ...] = get_args(CalendarEntityType)

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


def get_downloads_directory() -> Path:
    """現在のユーザーのダウンロードフォルダを返す。"""
    return Path.home() / "Downloads"


FOLDERS: dict[str, tuple[str, Callable[[], Path]]] = {
    "onedrive": ("OneDriveの保存先", get_onedrive_directory),
    "downloads": ("ダウンロードフォルダ", get_downloads_directory),
}


def open_folder(folder: FolderName) -> str:
    """許可済みのフォルダをExplorerで開く。

    Args:
        folder: 開くフォルダ。onedriveまたはdownloads。
    """
    if sys.platform != "win32":
        raise OSError("フォルダを開く操作はWindowsでのみ利用できます。")

    target = FOLDERS.get(folder)
    if target is None:
        allowed = ", ".join(FOLDERS)
        raise ValueError(
            f"未対応のフォルダです: {folder}（許可値: {allowed}）"
        )

    label, resolver = target
    path = resolver()
    if not path.is_dir():
        raise OSError(f"{label}が見つかりません: {path}")
    os.startfile(path)
    return f"{label}を開きました。\n{path}"


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


def escape_ics_text(text: str) -> str:
    """ICSのテキスト値をRFC 5545に従ってエスケープする。

    Args:
        text: SUMMARYやDESCRIPTIONへ埋め込むテキスト。
    """
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
    )


def format_calendar_ics(
    subject: str,
    start_time: str,
    body: str,
    uid: str,
    dtstamp: str,
) -> str:
    """予定1件分のICS本文を組み立てる（副作用なし）。

    Args:
        subject: 予定の件名。
        start_time: 開始日時（ISO 8601形式、例: 2026-07-01T14:00:00）。
        body: 予定の本文。空文字の場合はDESCRIPTIONを付けない。
        uid: 予定を一意に識別するUID。
        dtstamp: 生成時刻（UTC、YYYYMMDDTHHMMSSZ形式）。
    """
    start_dt = datetime.fromisoformat(start_time)
    end_dt = start_dt + timedelta(minutes=15)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//dev-slm-shortcut//JP",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}",
        f"SUMMARY:{escape_ics_text(subject)}",
    ]
    if body:
        lines.append(f"DESCRIPTION:{escape_ics_text(body)}")
    lines += [
        "BEGIN:VALARM",
        "TRIGGER:PT0S",
        "ACTION:DISPLAY",
        "DESCRIPTION:Reminder",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
    return "\r\n".join(lines)


def try_delete_ics(path: Path, attempts: int = 3, delay: float = 2.0) -> bool:
    """カレンダーアプリが読み込む間、数回リトライして一時ICSを削除する。

    Args:
        path: 削除するICSファイル。
        attempts: 試行回数。
        delay: 試行間隔（秒）。
    """
    for _ in range(attempts):
        try:
            path.unlink()
            return True
        except OSError:
            time.sleep(delay)
    return False


def register_pending_cleanup(path: Path) -> None:
    """削除できなかったICSを次回起動時の掃除対象として記録する。

    Args:
        path: 後で削除するICSファイル。
    """
    with CLEANUP_LIST_PATH.open("a", encoding="utf-8") as cleanup_list:
        cleanup_list.write(str(path) + "\n")


def run_pending_cleanup() -> None:
    """前回削除できなかったICS一時ファイルをまとめて削除する。"""
    if not CLEANUP_LIST_PATH.exists():
        return

    remaining = []
    for line in CLEANUP_LIST_PATH.read_text(encoding="utf-8").splitlines():
        path = Path(line.strip())
        if not path.exists():
            continue
        try:
            path.unlink()
        except OSError:
            remaining.append(line)

    if remaining:
        CLEANUP_LIST_PATH.write_text(
            "\n".join(remaining) + "\n", encoding="utf-8"
        )
    else:
        CLEANUP_LIST_PATH.unlink(missing_ok=True)


def create_calendar_task(
    start_time: str,
    entity_type: CalendarEntityType,
    title: str,
    body: str = "",
) -> str:
    """カレンダーの予定（タスク/リマインダ）をICSで作り、確認画面を開く。

    Args:
        start_time: 開始日時（ISO 8601形式、例: 2026-07-01T14:00:00）。
        entity_type: 予定の種別。タスクまたはリマインダのいずれか。
        title: 予定のタイトル。
        body: 予定の本文。依頼文で指定された本文、またはワークフローで
            直前の操作結果が渡る。両方ある場合は結合済みの文字列が渡る。
    """
    if sys.platform != "win32":
        raise OSError("カレンダー登録はWindowsでのみ利用できます。")

    normalized_title = title.strip()
    if not normalized_title:
        raise ValueError("タスク名が空です。")
    if entity_type not in CALENDAR_ENTITY_TYPES:
        allowed = ", ".join(CALENDAR_ENTITY_TYPES)
        raise ValueError(f"未対応の種別です: {entity_type}（許可値: {allowed}）")

    subject = f"【{entity_type}】{normalized_title}"
    uid = f"{uuid.uuid4()}@dev-slm-shortcut"
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ics_content = format_calendar_ics(subject, start_time, body, uid, dtstamp)

    with tempfile.NamedTemporaryFile(
        suffix=".ics", delete=False, mode="wb"
    ) as ics_file:
        ics_file.write(ics_content.encode("utf-8"))
        temp_path = Path(ics_file.name)

    os.startfile(temp_path)
    time.sleep(5)

    if not try_delete_ics(temp_path):
        register_pending_cleanup(temp_path)

    return f"カレンダーの確認画面を開きました: {subject}"


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
