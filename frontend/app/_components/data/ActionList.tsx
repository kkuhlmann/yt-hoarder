"use client"

import React from "react"
import { cn } from "@/lib/utils"

export type IconComponent = React.ComponentType<{
  className?: string
  title?: string
}>

export type ActionDescriptor<T> = {
  /** Stable id — also the React key and the header-strip key. */
  key: string
  title: string | ((row: T) => string)
  icon?: IconComponent
  /** Icon shown in the column header legend, when it differs from the row icon. */
  headerIcon?: IconComponent
  onClick?: (row: T) => void
  /** Leave the action out of this row entirely, e.g. one only the owner may use. */
  hidden?: (row: T) => boolean
  disabled?: (row: T) => boolean
  /** Tooltip shown instead of `title` while disabled — say *why* it's disabled. */
  disabledTitle?: string
  /** Hover/background classes for the button itself. */
  buttonClassName?: string
  /** Colour classes for the icon. Deliberately never a size — see ActionList. */
  iconClassName?: string
  /**
   * Escape hatch for actions that aren't a plain icon button (TranscriptStatus,
   * which swaps icons on hover and carries inline progress text).
   */
  render?: (row: T) => React.ReactNode
}

/**
 * One action button. Always stops propagation: every surface that renders these
 * also makes the row or card itself clickable, and an action must never start
 * playback as a side effect.
 */
export function ActionIconButton<T>({
  action,
  row,
}: {
  action: ActionDescriptor<T>
  row: T
}) {
  const Icon = action.icon
  const disabled = action.disabled?.(row) ?? false
  const title =
    disabled && action.disabledTitle
      ? action.disabledTitle
      : typeof action.title === "function"
        ? action.title(row)
        : action.title

  return (
    <button
      onClick={(e) => {
        e.stopPropagation()
        if (!disabled) action.onClick?.(row)
      }}
      disabled={disabled}
      title={title}
      className={cn(
        "p-1 rounded transition-colors disabled:opacity-30 disabled:cursor-default",
        action.buttonClassName,
      )}
    >
      {Icon ? <Icon className={action.iconClassName} /> : null}
    </button>
  )
}

/**
 * Renders a descriptor list as a bare fragment — the caller supplies the flex
 * container *and* the icon size.
 *
 * Icon sizing is deliberately not set here. The same action list renders at
 * h-4 in a table cell, h-3.5 in a mobile row and grid strip, and h-5 in the
 * touch popover; containers set it with `[&_svg]:h-4 [&_svg]:w-4`. Sizing icons
 * individually is exactly how the table and grid copies of this code drifted
 * apart in the first place.
 */
export function ActionList<T>({
  actions,
  row,
}: {
  actions: ActionDescriptor<T>[]
  row: T
}) {
  return (
    <>
      {actions.map((action) =>
        action.hidden?.(row) ? null : action.render ? (
          // Custom renderers may include non-button elements; the wrapper keeps
          // a stray click on those from reaching the row underneath.
          <span key={action.key} onClick={(e) => e.stopPropagation()}>
            {action.render(row)}
          </span>
        ) : (
          <ActionIconButton key={action.key} action={action} row={row} />
        ),
      )}
    </>
  )
}

/**
 * The actions column header: a legend of the same icons, in the same order, as
 * the buttons below it. Derived from the descriptor list so it cannot drift.
 */
export function actionsHeaderStrip<T>(actions: ActionDescriptor<T>[]) {
  return (
    <span className="flex items-center gap-1">
      {actions.map((action) => {
        const Icon = action.headerIcon ?? action.icon
        if (!Icon) return null
        return (
          <span key={action.key} className="p-1">
            <Icon
              className="h-4 w-4"
              title={
                typeof action.title === "function" ? action.key : action.title
              }
            />
          </span>
        )
      })}
    </span>
  )
}
