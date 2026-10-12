"""Native-looking payout-date controls with evidence-constrained mini calendars."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime

import streamlit as st


_COMPONENT_HTML = """
<div id="payout-date-picker"></div>
"""

_COMPONENT_CSS = """
:host { display: block; overflow: visible; }
.picker { color: var(--st-text-color); font-family: var(--st-font); position: relative; z-index: 10; }
.picker__label { color: var(--st-text-color); display: block; font-size: 0.88rem; margin-bottom: 0.35rem; }
.picker__input { align-items: center; background: var(--st-secondary-background-color); border: 1px solid var(--st-widget-border-color); border-radius: var(--st-base-radius); color: var(--st-text-color); cursor: pointer; display: flex; justify-content: space-between; min-height: 2.9rem; padding: 0.45rem 0.75rem; text-align: left; width: 100%; }
.picker__input:hover:not(:disabled) { border-color: var(--st-primary-color); }
.picker__input--open { border-color: var(--st-primary-color); box-shadow: 0 0 0 1px var(--st-primary-color); }
.picker__input:disabled { color: var(--st-gray-color); cursor: not-allowed; opacity: 0.55; }
.picker__value { font-size: 0.9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picker__icon { color: var(--st-gray-color); flex: 0 0 auto; height: 1.15rem; width: 1.15rem; }
.picker__panel { background: var(--st-background-color); border: 1px solid var(--st-widget-border-color); border-radius: calc(var(--st-base-radius) + 0.1rem); box-shadow: 0 0.75rem 2rem rgb(0 0 0 / 0.16); left: 0; margin-top: 0.45rem; padding: 0.7rem; position: absolute; top: 100%; width: 18.25rem; z-index: 1000; }
.picker__panel--end { left: auto; right: 0; }
.picker__header { align-items: center; display: flex; justify-content: space-between; margin-bottom: 0.4rem; }
.picker__month { font-size: 0.92rem; font-weight: 600; }
.picker__nav { background: transparent; border: 0; border-radius: var(--st-button-radius); color: var(--st-text-color); cursor: pointer; font-size: 1.3rem; height: 1.85rem; width: 1.85rem; }
.picker__nav:hover:not(:disabled) { background: var(--st-secondary-background-color); }
.picker__nav:disabled { color: var(--st-gray-color); cursor: not-allowed; opacity: 0.55; }
.picker__week, .picker__grid { display: grid; gap: 0.12rem; grid-template-columns: repeat(7, minmax(0, 1fr)); }
.picker__weekday { color: var(--st-gray-color); font-size: 0.7rem; padding: 0.2rem 0; text-align: center; }
.picker__day { background: transparent; border: 0; border-radius: 0.45rem; color: var(--st-text-color); cursor: pointer; font: inherit; font-size: 0.82rem; min-height: 1.85rem; padding: 0.15rem; }
.picker__day:hover:not(:disabled) { background: var(--st-secondary-background-color); }
.picker__day--outside { opacity: 0.65; }
.picker__day--disabled { color: var(--st-gray-color); cursor: not-allowed; opacity: 0.55; }
.picker__day--selected { background: var(--st-primary-color); color: var(--st-background-color); font-weight: 700; }
"""

_COMPONENT_JS = """
export default function(component) {
  const { data = {}, parentElement, setStateValue } = component;
  const root = parentElement.querySelector("#payout-date-picker");
  if (!root) return;

  const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const allowed = new Set(data.allowed_dates || []);
  const minDate = data.min_date || null;
  const maxDate = data.max_date || null;
  const value = data.value || null;
  const scopeId = `${data.scope_id || ""}|${minDate || ""}|${maxDate || ""}`;

  function iso(year, month, day) {
    return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  }

  function monthFor(candidate) {
    const valueForMonth = candidate || minDate || new Date().toISOString().slice(0, 10);
    const [year, month] = valueForMonth.split("-").map(Number);
    return { year, month: month - 1 };
  }

  function formatDate(candidate) {
    return candidate ? candidate.replaceAll("-", " / ") : "Select date";
  }

  if (!root._pickerState || root._pickerState.scopeId !== scopeId) {
    root._pickerState = { month: monthFor(value), open: false, scopeId };
  }

  function disabled(day) {
    return Boolean(data.disabled) || !allowed.has(day) || (minDate && day < minDate) || (maxDate && day > maxDate);
  }

  function makeButton(text, className, disabledValue, label, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = text;
    button.disabled = disabledValue;
    button.setAttribute("aria-label", label);
    if (onClick) button.onclick = onClick;
    return button;
  }

  function makeCalendarIcon() {
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.classList.add("picker__icon");
    icon.setAttribute("aria-hidden", "true");
    icon.setAttribute("fill", "none");
    icon.setAttribute("stroke", "currentColor");
    icon.setAttribute("stroke-width", "1.8");
    icon.setAttribute("viewBox", "0 0 24 24");
    const box = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    box.setAttribute("x", "3");
    box.setAttribute("y", "5");
    box.setAttribute("width", "18");
    box.setAttribute("height", "16");
    box.setAttribute("rx", "2");
    const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
    line.setAttribute("d", "M8 3v4m8-4v4M3 10h18");
    icon.append(box, line);
    return icon;
  }

  function render() {
    const state = root._pickerState;
    root.innerHTML = "";
    const picker = document.createElement("section");
    picker.className = "picker";
    picker.setAttribute("aria-label", data.label || "Payout date");

    const label = document.createElement("label");
    label.className = "picker__label";
    label.textContent = data.label || "Payout date";
    picker.appendChild(label);

    const input = document.createElement("button");
    input.type = "button";
    input.className = `picker__input${state.open ? " picker__input--open" : ""}`;
    input.disabled = Boolean(data.disabled);
    input.setAttribute("aria-expanded", String(state.open));
    input.setAttribute("aria-label", `${data.label || "Payout date"}: ${formatDate(value)}`);
    input.onclick = () => {
      state.open = !state.open;
      if (state.open) state.month = monthFor(value);
      render();
    };
    const fieldValue = document.createElement("span");
    fieldValue.className = "picker__value";
    fieldValue.textContent = formatDate(value);
    input.append(fieldValue, makeCalendarIcon());
    picker.appendChild(input);

    if (state.open) {
      const { year, month } = state.month;
      const panel = document.createElement("div");
      panel.className = `picker__panel${data.align_end ? " picker__panel--end" : ""}`;
      const header = document.createElement("div");
      header.className = "picker__header";
      const previous = new Date(year, month - 1, 1);
      const previousEndDate = new Date(year, month, 0);
      const previousEnd = iso(previousEndDate.getFullYear(), previousEndDate.getMonth(), previousEndDate.getDate());
      header.appendChild(makeButton("‹", "picker__nav", Boolean(minDate && previousEnd < minDate), "Previous month", () => {
        state.month = { year: previous.getFullYear(), month: previous.getMonth() };
        render();
      }));
      const title = document.createElement("div");
      title.className = "picker__month";
      title.textContent = `${monthNames[month]} ${year}`;
      header.appendChild(title);
      const next = new Date(year, month + 1, 1);
      const nextStart = iso(next.getFullYear(), next.getMonth(), 1);
      header.appendChild(makeButton("›", "picker__nav", Boolean(maxDate && nextStart > maxDate), "Next month", () => {
        state.month = { year: next.getFullYear(), month: next.getMonth() };
        render();
      }));
      panel.appendChild(header);

      const week = document.createElement("div");
      week.className = "picker__week";
      for (const weekday of weekdays) {
        const item = document.createElement("div");
        item.className = "picker__weekday";
        item.textContent = weekday;
        week.appendChild(item);
      }
      panel.appendChild(week);

      const grid = document.createElement("div");
      grid.className = "picker__grid";
      const first = new Date(year, month, 1);
      const gridStart = new Date(year, month, 1 - first.getDay());
      for (let index = 0; index < 42; index += 1) {
        const day = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + index);
        const dayIso = iso(day.getFullYear(), day.getMonth(), day.getDate());
        const isDisabled = disabled(dayIso);
        const classes = ["picker__day"];
        if (day.getMonth() !== month) classes.push("picker__day--outside");
        if (isDisabled) classes.push("picker__day--disabled");
        if (dayIso === value) classes.push("picker__day--selected");
        const button = makeButton(String(day.getDate()), classes.join(" "), isDisabled, dayIso, () => {
          state.open = false;
          setStateValue("value", dayIso);
        });
        grid.appendChild(button);
      }
      panel.appendChild(grid);
      picker.appendChild(panel);
    }

    root.appendChild(picker);
  }

  render();
}
"""


def _calendar_component():
    """Register in the active runtime so AppTest and the live app share it."""

    return st.components.v2.component(
        "invoicegather.available_payout_date_calendar",
        html=_COMPONENT_HTML,
        css=_COMPONENT_CSS,
        js=_COMPONENT_JS,
    )


def available_payout_date_picker(
    *,
    allowed_dates: Iterable[date],
    value: date | None,
    label: str,
    key: str,
    disabled: bool = False,
    scope_id: str,
    align_end: bool = False,
    on_value_change: Callable[[], None] | None = None,
) -> date | None:
    """Render one native-looking calendar input limited to committed dates."""

    dates = tuple(sorted(set(allowed_dates)))
    selected = _component_date_value(st.session_state.get(key), fallback=value)
    component_args = {
        "key": key,
        "data": {
            "label": label,
            "allowed_dates": [candidate.isoformat() for candidate in dates],
            "min_date": dates[0].isoformat() if dates else None,
            "max_date": dates[-1].isoformat() if dates else None,
            "value": selected.isoformat() if selected else None,
            "disabled": disabled,
            "scope_id": scope_id,
            "align_end": align_end,
        },
        "height": "content",
    }
    if on_value_change is not None:
        component_args["on_value_change"] = on_value_change
    _calendar_component()(**component_args)
    return selected


def component_date_value(value: object) -> date | None:
    """Decode the selected endpoint without accepting arbitrary injected data."""

    return _component_date_value(value, fallback=None)


def _component_date_value(component_state: object, *, fallback: date | None) -> date | None:
    if isinstance(component_state, Mapping):
        return _as_date(component_state.get("value"))
    return fallback


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
