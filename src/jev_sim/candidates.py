"""Parse sim-use UI JSON into a bounded candidate action list."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

# Roles that are usually not worth tapping (read-only chrome).
SKIP_ROLES = frozenset(
    {
        "StaticText",
        "TextView",
        "Image",
        "ImageView",
        "Group",
        "Other",
        "GenericElement",
        "Window",
        "Application",
        "ScrollView",
        "ScrollArea",
    }
)

# Meta actions always offered alongside UI taps (Jev Choice criteria keys).
META_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("done", "meta", "Goal is complete; stop the loop"),
    ("scroll-up", "gesture", "Scroll content up (page down)"),
    ("scroll-down", "gesture", "Scroll content down (page up)"),
    ("wait", "meta", "Wait briefly for animation / loading"),
)


@dataclass(frozen=True)
class ActionCandidate:
    """One bounded action Jev may choose."""

    id: str
    kind: str  # tap | gesture | meta
    selector: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    def criteria_blurb(self) -> str:
        base = f"{self.kind}: {self.label}" if self.label else self.kind
        if self.selector and self.selector != self.id:
            return f"{base} [{self.selector}]"
        return base


def _unwrap_ui(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept either a raw ExecutionResult or `{ok, data}` envelope."""
    if "entries" in payload or "outline" in payload:
        return payload
    data = payload.get("data")
    if isinstance(data, Mapping):
        return data
    return payload


def _entry_alias(entry: Mapping[str, Any]) -> int | None:
    aliases = entry.get("aliases") or {}
    if isinstance(aliases, Mapping) and "at" in aliases:
        try:
            return int(aliases["at"])
        except (TypeError, ValueError):
            return None
    if "at" in entry:
        try:
            return int(entry["at"])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    return None


def _is_tappable(entry: Mapping[str, Any]) -> bool:
    role = str(entry.get("role") or "")
    states = [str(s).lower() for s in (entry.get("states") or [])]
    if any(s in ("disabled", "not-enabled", "unavailable") for s in states):
        return False
    if role in SKIP_ROLES and not entry.get("uniqueId") and not entry.get("resource_id"):
        # Still allow StaticText that looks like a link/button via states.
        if not any("link" in s or "button" in s for s in states):
            return False
    label = str(entry.get("label") or "").strip()
    unique = entry.get("uniqueId") or entry.get("resource_id")
    if not label and not unique and role in SKIP_ROLES:
        return False
    return True


def entries_from_ui(ui: Mapping[str, Any] | Sequence[Any]) -> list[dict[str, Any]]:
    if isinstance(ui, Mapping):
        data = _unwrap_ui(ui)
        entries = data.get("entries")
        if isinstance(entries, list):
            return [e for e in entries if isinstance(e, Mapping)]
        return []
    return []


def outline_text(ui: Mapping[str, Any] | str | None) -> str:
    if ui is None:
        return ""
    if isinstance(ui, str):
        return ui
    data = _unwrap_ui(ui)
    outline = data.get("outline")
    if isinstance(outline, str) and outline.strip():
        return outline
    # Fall back to a compact reconstruction from entries.
    lines: list[str] = []
    app = data.get("appLabel") or data.get("app_label") or ""
    if app:
        lines.append(f"App: {app}")
    for entry in entries_from_ui(data):
        at = _entry_alias(entry)
        role = entry.get("role") or "?"
        label = entry.get("label") or ""
        prefix = f"@{at}" if at is not None else "-"
        lines.append(f"  {prefix}  {role}  {label!r}")
    return "\n".join(lines)


def build_candidates(
    ui: Mapping[str, Any] | Sequence[Any],
    *,
    include_meta: bool = True,
    max_taps: int = 80,
) -> list[ActionCandidate]:
    """Build `{id, kind, selector, label}` candidates from sim-use UI JSON."""
    candidates: list[ActionCandidate] = []
    seen: set[str] = set()

    for entry in entries_from_ui(ui):
        if len(candidates) >= max_taps:
            break
        if not _is_tappable(entry):
            continue
        at = _entry_alias(entry)
        if at is None:
            continue
        cid = f"@{at}"
        if cid in seen:
            continue
        seen.add(cid)
        role = str(entry.get("role") or "Element")
        label = str(entry.get("label") or "").strip() or role
        unique = entry.get("uniqueId") or entry.get("resource_id")
        if unique:
            label = f"{label} #{unique}"
        candidates.append(
            ActionCandidate(
                id=cid,
                kind="tap",
                selector=cid,
                label=label[:120],
            )
        )

    if include_meta:
        for mid, kind, label in META_ACTIONS:
            if mid not in seen:
                candidates.append(
                    ActionCandidate(id=mid, kind=kind, selector=mid, label=label)
                )
                seen.add(mid)

    return candidates


def choice_criteria(candidates: Iterable[ActionCandidate]) -> dict[str, str]:
    """Map candidate id → short description for a TypeSafe Choice question."""
    return {c.id: c.criteria_blurb() for c in candidates}
