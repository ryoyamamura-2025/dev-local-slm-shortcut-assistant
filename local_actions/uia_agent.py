import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Literal

from ollama import chat


MODEL = "qwen3:1.7b"
MAX_ELEMENTS = 120
DEFAULT_MAX_TURNS = 5
DEFAULT_SETTLE_SECONDS = 0.5
ElementAction = Literal["click", "set_text", "focus"]
LoopAction = Literal["click", "set_text", "focus", "wait", "done"]


LIST_ELEMENTS_SCRIPT = r"""
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$TargetTitle = $env:LOCAL_ACTIONS_UIA_TARGET_TITLE
$UseForeground = $env:LOCAL_ACTIONS_UIA_FOREGROUND -eq "1"
$MaxElements = [int]$env:LOCAL_ACTIONS_UIA_MAX_ELEMENTS

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class UiaWin32 {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@

function Get-TargetWindow {
    if ($UseForeground) {
        $handle = [UiaWin32]::GetForegroundWindow()
        if ($handle -eq [IntPtr]::Zero) { throw "Foreground window was not found." }
        return [System.Windows.Automation.AutomationElement]::FromHandle($handle)
    }

    if ([string]::IsNullOrWhiteSpace($TargetTitle)) {
        throw "Target title is required when foreground mode is disabled."
    }

    $windows = ([System.Windows.Automation.AutomationElement]::RootElement).FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    foreach ($window in $windows) {
        try {
            if ($window.Current.Name -like "*$TargetTitle*") { return $window }
        } catch {}
    }
    throw "Target window was not found: $TargetTitle"
}

function Convert-Element {
    param(
        [System.Windows.Automation.AutomationElement]$Element,
        [int]$Index
    )
    $rect = $Element.Current.BoundingRectangle
    $controlType = $Element.Current.ControlType.ProgrammaticName -replace "^ControlType\.", ""
    [PSCustomObject]@{
        id = "e$Index"
        index = $Index
        automation_id = $Element.Current.AutomationId
        type = $controlType
        name = $Element.Current.Name
        bounds = [PSCustomObject]@{
            x = [int]$rect.X
            y = [int]$rect.Y
            width = [int]$rect.Width
            height = [int]$rect.Height
        }
        enabled = [bool]$Element.Current.IsEnabled
    }
}

$target = Get-TargetWindow
$all = $target.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition
)

$elements = New-Object System.Collections.Generic.List[object]
$index = 0
foreach ($element in $all) {
    if ($elements.Count -ge $MaxElements) { break }
    try {
        $name = $element.Current.Name
        $automationId = $element.Current.AutomationId
        $rect = $element.Current.BoundingRectangle
        $controlType = $element.Current.ControlType.ProgrammaticName
        if ([string]::IsNullOrWhiteSpace($name) -and
            [string]::IsNullOrWhiteSpace($automationId)) {
            continue
        }
        if ($rect.Width -le 0 -or $rect.Height -le 0) {
            continue
        }
        $elements.Add((Convert-Element -Element $element -Index $index))
        $index += 1
    } catch {}
}

[PSCustomObject]@{
    window = [PSCustomObject]@{
        name = $target.Current.Name
        automation_id = $target.Current.AutomationId
        class_name = $target.Current.ClassName
    }
    elements = $elements
} | ConvertTo-Json -Depth 8 -Compress
"""


EXECUTE_PLAN_SCRIPT = r"""
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$TargetTitle = $env:LOCAL_ACTIONS_UIA_TARGET_TITLE
$UseForeground = $env:LOCAL_ACTIONS_UIA_FOREGROUND -eq "1"
$ElementIndex = [int]$env:LOCAL_ACTIONS_UIA_ELEMENT_INDEX
$Action = $env:LOCAL_ACTIONS_UIA_ACTION
$Text = $env:LOCAL_ACTIONS_UIA_TEXT

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class UiaWin32 {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, int dx, int dy, uint data, UIntPtr extraInfo);
}
"@

function Get-TargetWindow {
    if ($UseForeground) {
        $handle = [UiaWin32]::GetForegroundWindow()
        if ($handle -eq [IntPtr]::Zero) { throw "Foreground window was not found." }
        return [System.Windows.Automation.AutomationElement]::FromHandle($handle)
    }

    if ([string]::IsNullOrWhiteSpace($TargetTitle)) {
        throw "Target title is required when foreground mode is disabled."
    }

    $windows = ([System.Windows.Automation.AutomationElement]::RootElement).FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    foreach ($window in $windows) {
        try {
            if ($window.Current.Name -like "*$TargetTitle*") { return $window }
        } catch {}
    }
    throw "Target window was not found: $TargetTitle"
}

function Get-FilteredElements {
    param([System.Windows.Automation.AutomationElement]$Target)
    $all = $Target.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    $elements = New-Object System.Collections.Generic.List[System.Windows.Automation.AutomationElement]
    foreach ($element in $all) {
        try {
            $name = $element.Current.Name
            $automationId = $element.Current.AutomationId
            $rect = $element.Current.BoundingRectangle
            if ([string]::IsNullOrWhiteSpace($name) -and
                [string]::IsNullOrWhiteSpace($automationId)) {
                continue
            }
            if ($rect.Width -le 0 -or $rect.Height -le 0) {
                continue
            }
            $elements.Add($element)
        } catch {}
    }
    return $elements
}

function Invoke-Click {
    param([System.Windows.Automation.AutomationElement]$Element)

    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $target = $Element
    for ($i = 0; $i -lt 5; $i++) {
        try {
            $target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
            return
        } catch {}

        try {
            $point = $target.GetClickablePoint()
            [UiaWin32]::SetCursorPos([int]$point.X, [int]$point.Y) | Out-Null
            Start-Sleep -Milliseconds 80
            [UiaWin32]::mouse_event(0x0002, [int]$point.X, [int]$point.Y, 0, [UIntPtr]::Zero)
            Start-Sleep -Milliseconds 50
            [UiaWin32]::mouse_event(0x0004, [int]$point.X, [int]$point.Y, 0, [UIntPtr]::Zero)
            return
        } catch {}

        try {
            $rect = $target.Current.BoundingRectangle
            if ($rect.Width -gt 0 -and $rect.Height -gt 0) {
                $x = [int]($rect.X + ($rect.Width / 2))
                $y = [int]($rect.Y + ($rect.Height / 2))
                [UiaWin32]::SetCursorPos($x, $y) | Out-Null
                Start-Sleep -Milliseconds 80
                [UiaWin32]::mouse_event(0x0002, $x, $y, 0, [UIntPtr]::Zero)
                Start-Sleep -Milliseconds 50
                [UiaWin32]::mouse_event(0x0004, $x, $y, 0, [UIntPtr]::Zero)
                return
            }
        } catch {}

        $parent = $walker.GetParent($target)
        if ($null -eq $parent) { break }
        $target = $parent
    }

    throw "Element cannot be clicked: e$ElementIndex"
}

$target = Get-TargetWindow
$elements = Get-FilteredElements -Target $target
if ($ElementIndex -lt 0 -or $ElementIndex -ge $elements.Count) {
    throw "Element index is out of range: $ElementIndex"
}

$element = $elements[$ElementIndex]
if (-not $element.Current.IsEnabled) {
    throw "Target element is disabled: e$ElementIndex"
}

if ($Action -eq "focus") {
    $element.SetFocus()
} elseif ($Action -eq "click") {
    Invoke-Click -Element $element
} elseif ($Action -eq "set_text") {
    try {
        $pattern = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        $pattern.SetValue($Text)
    } catch {
        $element.SetFocus()
        [System.Windows.Forms.Clipboard]::SetText($Text)
        [System.Windows.Forms.SendKeys]::SendWait("^a")
        [System.Windows.Forms.SendKeys]::SendWait("^v")
    }
} else {
    throw "Unsupported UIA action: $Action"
}

[PSCustomObject]@{
    element_id = "e$ElementIndex"
    action = $Action
    name = $element.Current.Name
    type = ($element.Current.ControlType.ProgrammaticName -replace "^ControlType\.", "")
} | ConvertTo-Json -Compress
"""


@dataclass(frozen=True)
class UiaElement:
    """A compact UI Automation element description for the SLM."""

    id: str
    index: int
    type: str
    name: str
    bounds: dict[str, int]
    enabled: bool
    automation_id: str = ""


@dataclass(frozen=True)
class UiaPlan:
    """A single allowed UI Automation action selected by the SLM."""

    element_id: str
    action: ElementAction
    text: str = ""
    reason: str = ""


@dataclass(frozen=True)
class UiaLoopDecision:
    """A single loop decision selected from the current UI snapshot."""

    action: LoopAction
    element_id: str = ""
    text: str = ""
    seconds: float = DEFAULT_SETTLE_SECONDS


@dataclass(frozen=True)
class UiaSnapshotDiff:
    """A compact difference between two UI Automation snapshots."""

    added: list[UiaElement]
    removed: list[UiaElement]
    changed: list[UiaElement]


def _run_powershell(script: str, environment: dict[str, str]) -> str:
    """Run a fixed PowerShell UI Automation script and return stdout."""
    if sys.platform != "win32":
        raise OSError("UI Automation is only available on Windows.")

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Sta",
            "-Command",
            script,
        ],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        raise OSError("UI Automation script failed." + (f"\n{detail}" if detail else ""))
    return completed.stdout


def _environment(
    window_title: str,
    foreground: bool,
    max_elements: int = MAX_ELEMENTS,
) -> dict[str, str]:
    """Build environment variables for fixed UI Automation scripts."""
    environment = dict(os.environ)
    environment["LOCAL_ACTIONS_UIA_TARGET_TITLE"] = window_title
    environment["LOCAL_ACTIONS_UIA_FOREGROUND"] = "1" if foreground else "0"
    environment["LOCAL_ACTIONS_UIA_MAX_ELEMENTS"] = str(max_elements)
    return environment


def list_window_elements(
    window_title: str = "",
    foreground: bool = True,
    max_elements: int = MAX_ELEMENTS,
) -> list[UiaElement]:
    """Return compact UI Automation elements from a target window."""
    output = _run_powershell(
        LIST_ELEMENTS_SCRIPT,
        _environment(window_title, foreground, max_elements),
    )
    try:
        payload = json.loads(output)
        raw_elements = payload["elements"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise OSError("UI Automation returned invalid JSON.") from error

    elements: list[UiaElement] = []
    for raw in raw_elements:
        elements.append(
            UiaElement(
                id=str(raw["id"]),
                index=int(raw["index"]),
                automation_id=str(raw.get("automation_id", "")),
                type=str(raw["type"]),
                name=str(raw.get("name", "")),
                bounds={key: int(value) for key, value in raw["bounds"].items()},
                enabled=bool(raw["enabled"]),
            )
        )
    return elements


def build_uia_plan_schema() -> dict[str, Any]:
    """Build the JSON schema for a single SLM-selected UI Automation action."""
    return {
        "type": "object",
        "required": ["element_id", "action"],
        "additionalProperties": False,
        "properties": {
            "element_id": {"type": "string", "pattern": "^e[0-9]+$"},
            "action": {"type": "string", "enum": ["click", "set_text", "focus"]},
            "text": {"type": "string"},
            "reason": {"type": "string"},
        },
    }


def serialize_elements(elements: list[UiaElement]) -> list[dict[str, Any]]:
    """Return UI Automation elements as JSON-serializable dictionaries."""
    return [
        {
            "id": element.id,
            "index": element.index,
            "type": element.type,
            "name": element.name,
            "bounds": element.bounds,
            "enabled": element.enabled,
            "automation_id": element.automation_id,
        }
        for element in elements
    ]


def _element_catalog(elements: list[UiaElement]) -> str:
    """Format UI Automation elements as compact JSON lines for the SLM."""
    return "\n".join(
        json.dumps(element, ensure_ascii=False)
        for element in serialize_elements(elements)
    )

def _score_element_for_request(request: str, element: UiaElement) -> int:
    """Score how likely an element name/type matches a natural language request."""
    request_text = request.casefold()
    name = element.name.casefold()
    automation_id = element.automation_id.casefold()
    haystack = f"{name} {automation_id} {element.type.casefold()}"
    score = 0
    for token in re.findall(r"[\w\u3040-\u30ff\u3400-\u9fff]+", request_text):
        if len(token) >= 2 and token in haystack:
            score += 30 + len(token)
    score += sum(
        1
        for character in set(request_text)
        if character.strip() and character in haystack
    )
    if element.type in {"Button", "SplitButton", "MenuItem", "Hyperlink"}:
        score += 4
    if element.type in {"Edit", "Document"}:
        score += 3
    if not element.enabled:
        score -= 100
    return score


def candidate_elements_for_request(
    request: str,
    elements: list[UiaElement],
    limit: int = 50,
) -> list[UiaElement]:
    """Return a smaller, request-biased element list for the SLM."""
    scored = [
        (_score_element_for_request(request, element), element.index, element)
        for element in elements
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    candidates = [element for score, _, element in scored if score > 0][:limit]
    if candidates:
        return candidates
    return elements[:limit]


def _element_key(element: UiaElement) -> tuple[Any, ...]:
    """Return a best-effort stable key for matching elements across snapshots."""
    if element.automation_id:
        return ("automation_id", element.type, element.automation_id)
    bounds = element.bounds
    return (
        "visual",
        element.type,
        element.name,
        bounds.get("x", 0),
        bounds.get("y", 0),
        bounds.get("width", 0),
        bounds.get("height", 0),
    )


def _element_state(element: UiaElement) -> tuple[Any, ...]:
    """Return comparable state for detecting changed matched elements."""
    bounds = element.bounds
    return (
        element.type,
        element.name,
        element.automation_id,
        element.enabled,
        bounds.get("x", 0),
        bounds.get("y", 0),
        bounds.get("width", 0),
        bounds.get("height", 0),
    )


def diff_snapshots(
    previous: list[UiaElement] | None,
    current: list[UiaElement],
) -> UiaSnapshotDiff:
    """Compare UI Automation snapshots and return added, removed, and changed elements."""
    if previous is None:
        return UiaSnapshotDiff(added=current, removed=[], changed=[])

    previous_by_key = {_element_key(element): element for element in previous}
    current_by_key = {_element_key(element): element for element in current}

    added = [
        element for element in current
        if _element_key(element) not in previous_by_key
    ]
    removed = [
        element for element in previous
        if _element_key(element) not in current_by_key
    ]
    changed = []
    for element in current:
        key = _element_key(element)
        old = previous_by_key.get(key)
        if old is not None and _element_state(old) != _element_state(element):
            changed.append(element)

    return UiaSnapshotDiff(added=added, removed=removed, changed=changed)


def _format_element_lines(elements: list[UiaElement], limit: int = 30) -> str:
    """Format a bounded element list as compact JSON lines."""
    if not elements:
        return "- none"
    visible = elements[:limit]
    lines = [json.dumps(element, ensure_ascii=False) for element in serialize_elements(visible)]
    remaining = len(elements) - len(visible)
    if remaining > 0:
        lines.append(f"... {remaining} more")
    return "\n".join(lines)


def format_snapshot_diff(diff: UiaSnapshotDiff, limit: int = 20) -> str:
    """Format UI changes so the SLM can attend to them before current candidates."""
    return (
        "Added:\n"
        f"{_format_element_lines(diff.added, limit)}\n\n"
        "Changed:\n"
        f"{_format_element_lines(diff.changed, limit)}\n\n"
        "Removed:\n"
        f"{_format_element_lines(diff.removed, limit)}"
    )


def loop_candidate_elements(
    request: str,
    elements: list[UiaElement],
    diff: UiaSnapshotDiff,
    limit: int = 70,
) -> list[UiaElement]:
    """Prioritize changed UI while keeping enough current context for the next action."""
    ordered: list[UiaElement] = []
    seen: set[str] = set()

    def add_many(candidates: list[UiaElement]) -> None:
        for element in candidates:
            if element.id not in seen and element in elements:
                ordered.append(element)
                seen.add(element.id)

    add_many(diff.added)
    add_many(diff.changed)
    add_many(candidate_elements_for_request(request, elements, limit=limit))
    add_many([
        element for element in elements
        if element.enabled and element.type in {"Button", "SplitButton", "MenuItem", "Hyperlink", "Edit", "Document"}
    ])
    add_many(elements)
    return ordered[:limit]


def build_loop_decision_schema() -> dict[str, Any]:
    """Build the JSON schema for one UI Automation loop decision."""
    return {
        "type": "object",
        "required": ["action"],
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["click", "set_text", "focus", "wait", "done"]},
            "element_id": {"type": "string"},
            "text": {"type": "string"},
            "seconds": {"type": "number"},
        },
    }


def validate_loop_decision(
    payload: dict[str, Any],
    elements: list[UiaElement],
) -> UiaLoopDecision:
    """Validate a loop decision against the current UI snapshot."""
    action = str(payload.get("action", ""))
    if action not in {"click", "set_text", "focus", "wait", "done"}:
        raise ValueError(f"Unsupported UI Automation loop action: {action}")

    element_id = str(payload.get("element_id", ""))
    if action in {"click", "set_text", "focus"}:
        if not re.fullmatch(r"e[0-9]+", element_id):
            raise ValueError(f"Invalid element_id: {element_id}")
        if element_id not in {element.id for element in elements}:
            raise ValueError(f"Unknown element_id in current snapshot: {element_id}")
    else:
        element_id = ""

    text = str(payload.get("text", ""))
    if action == "set_text" and not text:
        raise ValueError("set_text requires text.")
    if action != "set_text":
        text = ""

    try:
        seconds = float(payload.get("seconds", DEFAULT_SETTLE_SECONDS))
    except (TypeError, ValueError):
        seconds = DEFAULT_SETTLE_SECONDS
    seconds = min(max(seconds, 0.1), 5.0)

    return UiaLoopDecision(
        action=action,  # type: ignore[arg-type]
        element_id=element_id,
        text=text,
        seconds=seconds,
    )


def select_uia_loop_decision(
    request: str,
    elements: list[UiaElement],
    diff: UiaSnapshotDiff,
    last_action: dict[str, Any] | None,
    turn: int,
    max_turns: int,
    model: str = MODEL,
) -> UiaLoopDecision:
    """Ask the SLM to choose the next loop action from the current UI snapshot."""
    if not elements:
        raise ValueError("No UI Automation elements were found.")

    candidates = loop_candidate_elements(request, elements, diff)
    response = chat(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You control Windows UI Automation one safe action at a time. "
                    "Every element_id is valid only for the current snapshot. "
                    "UI changes from the previous turn are highlighted, but choose only from Current elements. "
                    "Return only JSON matching the schema. "
                    "Allowed actions are click, set_text, focus, wait, and done. "
                    "Use done only when the user goal is complete or no useful safe action remains. "
                    "Use wait only when the UI is likely still loading. "
                    "Do not invent shell commands, Python code, selectors, coordinates, or extra steps."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Goal:\n{request}\n\n"
                    f"Turn: {turn}/{max_turns}\n\n"
                    "Last action:\n"
                    f"{json.dumps(last_action or {}, ensure_ascii=False)}\n\n"
                    "UI changes since last observation:\n"
                    f"{format_snapshot_diff(diff)}\n\n"
                    "Current elements, one JSON object per line:\n"
                    f"{_element_catalog(candidates)}"
                ),
            },
        ],
        format=build_loop_decision_schema(),
        think=False,
        options={"temperature": 0},
    )

    try:
        payload = json.loads(response.message.content)
    except (json.JSONDecodeError, TypeError, AttributeError) as error:
        raise ValueError("SLM returned invalid UI Automation loop JSON.") from error

    return validate_loop_decision(payload, elements)


def _decision_to_plan(decision: UiaLoopDecision) -> UiaPlan:
    """Convert an executable loop decision to the existing one-shot plan type."""
    if decision.action not in {"click", "set_text", "focus"}:
        raise ValueError(f"Loop action is not executable as a UIA plan: {decision.action}")
    return UiaPlan(
        element_id=decision.element_id,
        action=decision.action,  # type: ignore[arg-type]
        text=decision.text,
        reason="",
    )


def select_uia_plan(
    request: str,
    elements: list[UiaElement],
    model: str = MODEL,
) -> UiaPlan:
    """Ask the SLM to choose one element and one allowed UI action."""
    if not elements:
        raise ValueError("No UI Automation elements were found.")

    candidates = candidate_elements_for_request(request, elements)

    response = chat(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You choose exactly one UI Automation action for the user request. "
                    "The user request is the highest-priority signal. "
                    "Return only JSON matching the schema. "
                    "Choose element_id from the provided candidate list only. "
                    "Allowed actions are click, set_text, and focus. "
                    "Use set_text only when the user asks to enter text, and put that exact text in text. "
                    "For click or focus, leave text empty. "
                    "Do not choose an unrelated repeated/history item just because it is visible. "
                    "Do not invent shell commands, Python code, selectors, or extra steps."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Request:\n{request}\n\n"
                    "Candidate elements, one JSON object per line:\n"
                    f"{_element_catalog(candidates)}"
                ),
            },
        ],
        format=build_uia_plan_schema(),
        think=False,
        options={"temperature": 0},
    )

    try:
        payload = json.loads(response.message.content)
    except (json.JSONDecodeError, TypeError, AttributeError) as error:
        raise ValueError("SLM returned invalid UI Automation JSON.") from error

    return validate_uia_plan(payload, elements)


def validate_uia_plan(payload: dict[str, Any], elements: list[UiaElement]) -> UiaPlan:
    """Validate an SLM UI Automation plan against the observed elements."""
    element_id = str(payload.get("element_id", ""))
    if not re.fullmatch(r"e[0-9]+", element_id):
        raise ValueError(f"Invalid element_id: {element_id}")
    if element_id not in {element.id for element in elements}:
        raise ValueError(f"Unknown element_id: {element_id}")

    action = str(payload.get("action", ""))
    if action not in {"click", "set_text", "focus"}:
        raise ValueError(f"Unsupported UI Automation action: {action}")

    text = str(payload.get("text", ""))
    if action == "set_text" and not text:
        raise ValueError("set_text requires text.")
    if action != "set_text":
        text = ""

    return UiaPlan(
        element_id=element_id,
        action=action,  # type: ignore[arg-type]
        text=text,
        reason=str(payload.get("reason", "")),
    )


def execute_uia_plan(
    plan: UiaPlan,
    elements: list[UiaElement],
    window_title: str = "",
    foreground: bool = True,
) -> str:
    """Execute a validated UI Automation plan with fixed Python-side actions."""
    element_by_id = {element.id: element for element in elements}
    element = element_by_id.get(plan.element_id)
    if element is None:
        raise ValueError(f"Unknown element_id: {plan.element_id}")
    if not element.enabled:
        raise ValueError(f"Target element is disabled: {plan.element_id}")

    environment = _environment(window_title, foreground)
    environment["LOCAL_ACTIONS_UIA_ELEMENT_INDEX"] = str(element.index)
    environment["LOCAL_ACTIONS_UIA_ACTION"] = plan.action
    environment["LOCAL_ACTIONS_UIA_TEXT"] = plan.text
    output = _run_powershell(EXECUTE_PLAN_SCRIPT, environment)
    return output.strip()


def run_uia_agent(
    request: str,
    window_title: str = "",
    foreground: bool = True,
    dry_run: bool = False,
) -> str:
    """Collect UI elements, ask the SLM for one action, and optionally execute it."""
    elements = list_window_elements(window_title=window_title, foreground=foreground)
    plan = select_uia_plan(request, elements)
    selection = {
        "element_id": plan.element_id,
        "action": plan.action,
        "text": plan.text,
        "reason": plan.reason,
    }
    print(json.dumps(selection, ensure_ascii=False, indent=2))
    if dry_run:
        return json.dumps(selection, ensure_ascii=False)
    return execute_uia_plan(
        plan,
        elements,
        window_title=window_title,
        foreground=foreground,
    )




def _element_snapshot(element: UiaElement) -> dict[str, Any]:
    """Return stable descriptive data for an element selected in a past snapshot."""
    return {
        "id_at_observation": element.id,
        "index_at_observation": element.index,
        "type": element.type,
        "name": element.name,
        "automation_id": element.automation_id,
        "bounds": element.bounds,
        "enabled": element.enabled,
    }


def _describe_loop_decision(
    decision: UiaLoopDecision,
    target: UiaElement | None,
) -> str:
    """Build a deterministic action description for logs and the next turn."""
    if decision.action == "done":
        return "Marked the UI task as done."
    if decision.action == "wait":
        return f"Waited {decision.seconds:.1f} seconds before observing the UI again."
    if target is None:
        return f"Selected {decision.action} on {decision.element_id}."

    label = target.name or target.automation_id or target.id
    base = f"{decision.action} {target.type} {json.dumps(label, ensure_ascii=False)}"
    if target.automation_id:
        base += f" automation_id={json.dumps(target.automation_id, ensure_ascii=False)}"
    if decision.action == "set_text":
        base += f" text={json.dumps(decision.text, ensure_ascii=False)}"
    return base


def _loop_decision_payload(
    turn: int,
    decision: UiaLoopDecision,
    elements: list[UiaElement],
) -> dict[str, Any]:
    """Return a loop decision payload useful for both logs and the next SLM turn."""
    target = {element.id: element for element in elements}.get(decision.element_id)
    payload: dict[str, Any] = {
        "turn": turn,
        "action": decision.action,
        "description": _describe_loop_decision(decision, target),
        "element_id": decision.element_id,
        "target": _element_snapshot(target) if target else None,
        "text": decision.text,
        "seconds": decision.seconds,
    }
    return payload


def run_uia_agent_loop(
    request: str,
    window_title: str = "",
    foreground: bool = True,
    max_turns: int = DEFAULT_MAX_TURNS,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
) -> str:
    """Run an experimental UI Automation agent loop with fresh observations each turn."""
    previous_elements: list[UiaElement] | None = None
    elements = list_window_elements(window_title=window_title, foreground=foreground)
    last_action: dict[str, Any] | None = None
    last_result = ""

    for turn in range(1, max_turns + 1):
        diff = diff_snapshots(previous_elements, elements)
        decision = select_uia_loop_decision(
            request=request,
            elements=elements,
            diff=diff,
            last_action=last_action,
            turn=turn,
            max_turns=max_turns,
        )
        decision_payload = _loop_decision_payload(turn, decision, elements)
        print(json.dumps(decision_payload, ensure_ascii=False, indent=2))

        if decision.action == "done":
            return decision_payload["description"] or last_result

        previous_elements = elements
        if decision.action == "wait":
            time.sleep(decision.seconds)
            last_action = decision_payload
        else:
            result = execute_uia_plan(
                _decision_to_plan(decision),
                elements,
                window_title=window_title,
                foreground=foreground,
            )
            last_result = result
            last_action = decision_payload | {"result": result}
            time.sleep(settle_seconds)

        elements = list_window_elements(window_title=window_title, foreground=foreground)

    return last_result or f"Reached max turns: {max_turns}"


def main() -> None:
    """Run the experimental UI Automation agent from the command line."""
    parser = argparse.ArgumentParser(
        description="Experimental UI Automation agent for Local Actions."
    )
    parser.add_argument("request", nargs="*", help="Natural language request.")
    parser.add_argument("--title", default="", help="Target window title substring.")
    parser.add_argument(
        "--foreground",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the foreground window as the target.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not execute.")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run the experimental observe/act/reobserve agent loop.",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_TURNS,
        help="Maximum UIA loop turns.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=DEFAULT_SETTLE_SECONDS,
        help="Seconds to wait after each UI action before reobserving.",
    )
    parser.add_argument(
        "--list-elements",
        action="store_true",
        help="Print observed UI Automation elements and exit without SLM or actions.",
    )
    args = parser.parse_args()

    if args.list_elements:
        elements = list_window_elements(
            window_title=args.title,
            foreground=args.foreground,
        )
        print(json.dumps(serialize_elements(elements), ensure_ascii=False, indent=2))
        return

    request = " ".join(args.request).strip() or input("Instruction> ").strip()
    if args.loop:
        if args.dry_run:
            raise SystemExit("--dry-run is only supported by the one-shot UIA agent.")
        result = run_uia_agent_loop(
            request,
            window_title=args.title,
            foreground=args.foreground,
            max_turns=args.max_turns,
            settle_seconds=args.settle_seconds,
        )
    else:
        result = run_uia_agent(
            request,
            window_title=args.title,
            foreground=args.foreground,
            dry_run=args.dry_run,
        )
    if result:
        print(result)


if __name__ == "__main__":
    main()






