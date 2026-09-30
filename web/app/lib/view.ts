"use client";

import { useSyncExternalStore } from "react";

/**
 * Which part of the dashboard you are looking at.
 *
 * The address carries it, not React state: `#videos` is a place, so the back
 * button returns to the last one, a refresh keeps you where you were, and a
 * link to a view can be sent to someone. None of that is true of a variable
 * inside a component.
 */
export const VIEWS = [
  "overview",
  "videos",
  "create",
  "achievements",
  "settings",
] as const;

export type View = (typeof VIEWS)[number];

/**
 * The view named by the hash, or the overview.
 *
 * "/dashboard", "#overview" and "#typo" all mean the overview. An unknown
 * hash falls back rather than rendering nothing -- a bad link should land
 * somewhere sensible, not on a blank page.
 */
export function viewFromHash(): View {
  const name = window.location.hash.replace(/^#/, "");
  return (VIEWS as readonly string[]).includes(name) ? (name as View) : "overview";
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

/**
 * The current view, kept in step with the address.
 *
 * `useSyncExternalStore` rather than `useState` plus an effect because it is
 * the primitive for reading something outside React: it reads once on mount,
 * re-reads whenever the store says it changed, and asks for a server
 * snapshot instead of touching `window` while rendering on the server. The
 * back button needs no code of its own -- it fires `hashchange` like any
 * other navigation.
 */
export function useView(): View {
  return useSyncExternalStore(subscribe, viewFromHash, () => "overview");
}

/** Go to a view. Assigning the hash is what pushes the history entry. */
export function goToView(view: View): void {
  window.location.hash = view;
}
