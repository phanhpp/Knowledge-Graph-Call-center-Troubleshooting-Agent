"use client";

import { useRef } from "react";

const KEY_STEP = 24; // pixels per arrow-key press

/**
 * A drag handle between two panels (role="separator"), for the mouse and the keyboard.
 *
 * `grow` is the direction that makes the panel bigger: "left" for a panel on the right of a vertical
 * bar (dragging the bar left widens it), "down" for a panel above a horizontal bar. Double-click
 * resets. Pointer capture keeps the drag going when the pointer outruns the thin bar.
 */
export function ResizeHandle({
  label,
  grow,
  value,
  min,
  max,
  onResize,
  onReset,
}: {
  label: string;
  grow: "left" | "down";
  value: number;
  min: number;
  max: number;
  onResize: (next: number) => void;
  onReset: () => void;
}) {
  const start = useRef<{ pointer: number; value: number } | null>(null);
  const vertical = grow === "left"; // a vertical bar, dragged sideways

  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation={vertical ? "vertical" : "horizontal"}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      title={`${label} (double-click to reset)`}
      className={`shrink-0 touch-none bg-border/40 transition-colors hover:bg-primary focus:bg-primary focus:outline-none ${
        vertical ? "w-1.5 cursor-col-resize" : "my-2 h-1.5 cursor-row-resize rounded"
      }`}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        start.current = { pointer: vertical ? event.clientX : event.clientY, value };
      }}
      onPointerMove={(event) => {
        if (!start.current) return;
        const position = vertical ? event.clientX : event.clientY;
        const moved = vertical ? start.current.pointer - position : position - start.current.pointer;
        onResize(start.current.value + moved);
      }}
      onPointerUp={(event) => {
        start.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onDoubleClick={onReset}
      onKeyDown={(event) => {
        const bigger = vertical ? "ArrowLeft" : "ArrowDown";
        const smaller = vertical ? "ArrowRight" : "ArrowUp";
        if (event.key === bigger) onResize(value + KEY_STEP);
        else if (event.key === smaller) onResize(value - KEY_STEP);
        else return;
        event.preventDefault();
      }}
    />
  );
}
