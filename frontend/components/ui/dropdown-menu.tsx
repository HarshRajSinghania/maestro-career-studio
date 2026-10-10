"use client"

import * as React from "react"
import { Menu as MenuPrimitive } from "@base-ui/react/menu"

import { POPUP_SURFACE } from "@/components/ui/popover"
import { focusIfDropped } from "@/hooks/use-focus-return"
import { cn } from "@/lib/utils"

/**
 * Focus returns to the trigger when the menu closes (APG menu button). Base UI
 * does that after a key press but nowhere after a click (the item it left is
 * gone), so once the popup has unmounted a focus that fell to <body> moves to
 * the trigger. An overlay an item opened keeps its focus: that focus is not
 * <body>, and a modal one hides the page (`aria-hidden`), trigger included.
 *
 * Base UI timing this depends on (1.4.1): `onOpenChangeComplete(false)` runs
 * just BEFORE the popup unmounts, with focus still on the item, so the check
 * waits a task. After an upgrade, re-check in the browser: a click on a
 * /templates card's ⋯ → Duplicate lands on ⋯, and the studio's ⋯ → History /
 * Rebuild start inside the sheet / confirm, not on ⋯.
 */
function DropdownMenu({
  onOpenChange,
  onOpenChangeComplete,
  ...props
}: MenuPrimitive.Root.Props) {
  const trigger = React.useRef<Element | undefined>(undefined)
  return (
    <MenuPrimitive.Root
      data-slot="dropdown-menu"
      onOpenChange={(open, details) => {
        if (open) trigger.current = details.trigger
        onOpenChange?.(open, details)
      }}
      onOpenChangeComplete={(open) => {
        onOpenChangeComplete?.(open)
        // Called just BEFORE the popup unmounts (focus is still on the item).
        if (!open) setTimeout(() => returnToTrigger(trigger.current), 0)
      }}
      {...props}
    />
  )
}

function returnToTrigger(trigger: Element | undefined) {
  if (!(trigger instanceof HTMLElement) || trigger.closest('[aria-hidden="true"], [inert]')) return
  focusIfDropped(trigger)
}

function DropdownMenuTrigger({ ...props }: MenuPrimitive.Trigger.Props) {
  return <MenuPrimitive.Trigger data-slot="dropdown-menu-trigger" {...props} />
}

function DropdownMenuContent({
  align = "start",
  alignOffset = 0,
  side = "bottom",
  sideOffset = 4,
  className,
  ...props
}: MenuPrimitive.Popup.Props &
  Pick<
    MenuPrimitive.Positioner.Props,
    "align" | "alignOffset" | "side" | "sideOffset"
  >) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Positioner
        className="isolate z-50 outline-none"
        align={align}
        alignOffset={alignOffset}
        side={side}
        sideOffset={sideOffset}
      >
        {/* Adds width + row padding on top of the shared surface: a menu is
            anchored to its trigger's width and pads for menuitem rows. */}
        <MenuPrimitive.Popup
          data-slot="dropdown-menu-content"
          className={cn(POPUP_SURFACE, "w-(--anchor-width) rounded-corner-xs p-1", className)}
          {...props}
        />
      </MenuPrimitive.Positioner>
    </MenuPrimitive.Portal>
  )
}

function DropdownMenuItem({
  className,
  inset,
  variant = "default",
  ...props
}: MenuPrimitive.Item.Props & {
  inset?: boolean
  variant?: "default" | "destructive"
}) {
  return (
    <MenuPrimitive.Item
      data-slot="dropdown-menu-item"
      data-inset={inset}
      data-variant={variant}
      className={cn(
        "group/dropdown-menu-item relative flex cursor-default items-center gap-1.5 rounded-corner-xs px-1.5 py-1 text-body-medium outline-hidden select-none focus:bg-accent focus:text-accent-foreground not-data-[variant=destructive]:focus:**:text-accent-foreground data-inset:pl-7 data-[variant=destructive]:text-destructive data-[variant=destructive]:focus:bg-destructive/10 data-[variant=destructive]:focus:text-destructive dark:data-[variant=destructive]:focus:bg-destructive/20 data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 data-[variant=destructive]:*:[svg]:text-destructive",
        className
      )}
      {...props}
    />
  )
}

function DropdownMenuSeparator({
  className,
  ...props
}: MenuPrimitive.Separator.Props) {
  return (
    <MenuPrimitive.Separator
      data-slot="dropdown-menu-separator"
      className={cn("-mx-1 my-1 h-px bg-border", className)}
      {...props}
    />
  )
}

export {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
}
