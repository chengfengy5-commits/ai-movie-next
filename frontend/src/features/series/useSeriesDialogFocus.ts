import { useCallback, useLayoutEffect, useRef, type RefObject } from "react";

interface SeriesDialogFocusOptions {
  active: boolean;
  canDismiss: boolean;
  onDismiss(): void;
}

interface SeriesDialogFocusControl {
  dialogRef: RefObject<HTMLElement | null>;
  dismiss(): void;
  markDismissed(): void;
}

function focusableElements(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  ));
}

export function useSeriesDialogFocus({
  active,
  canDismiss,
  onDismiss,
}: SeriesDialogFocusOptions): SeriesDialogFocusControl {
  const dialogRef = useRef<HTMLElement | null>(null);
  const dismissalRef = useRef({ canDismiss, onDismiss });
  dismissalRef.current = { canDismiss, onDismiss };
  const shouldRestoreFocusRef = useRef(false);

  const dismiss = useCallback((): void => {
    if (!dismissalRef.current.canDismiss) {
      return;
    }
    shouldRestoreFocusRef.current = true;
    dismissalRef.current.onDismiss();
  }, []);

  const markDismissed = useCallback((): void => {
    shouldRestoreFocusRef.current = true;
  }, []);

  useLayoutEffect(() => {
    if (!active) {
      return;
    }

    const dialog = dialogRef.current;
    if (dialog === null) {
      return;
    }

    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const inertStates: Array<{ element: HTMLElement; wasInert: boolean }> = [];
    let branch: HTMLElement = dialog;
    while (branch.parentElement !== null && branch.parentElement !== document.body) {
      const parent = branch.parentElement;
      for (const child of Array.from(parent.children)) {
        if (!(child instanceof HTMLElement) || child === branch) {
          continue;
        }
        inertStates.push({ element: child, wasInert: child.hasAttribute("inert") });
        child.setAttribute("inert", "");
      }
      branch = parent;
    }
    for (const child of Array.from(document.body.children)) {
      if (!(child instanceof HTMLElement) || child === branch) {
        continue;
      }
      inertStates.push({ element: child, wasInert: child.hasAttribute("inert") });
      child.setAttribute("inert", "");
    }

    const focusFirst = (): void => {
      const focusables = focusableElements(dialog);
      (focusables[0] ?? dialog).focus();
    };

    focusFirst();

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        dismiss();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }

      const focusables = focusableElements(dialog);
      if (focusables.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const current = document.activeElement;
      if (!dialog.contains(current)) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus();
      } else if (event.shiftKey && (current === first || current === dialog)) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && current === last) {
        event.preventDefault();
        first?.focus();
      }
    };

    const onFocusIn = (event: FocusEvent): void => {
      if (event.target instanceof Node && !dialog.contains(event.target)) {
        focusFirst();
      }
    };

    const onClick = (event: MouseEvent): void => {
      if (event.target instanceof Node && !dialog.contains(event.target)) {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("focusin", onFocusIn, true);
    document.addEventListener("click", onClick, true);

    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("focusin", onFocusIn, true);
      document.removeEventListener("click", onClick, true);
      for (const { element, wasInert } of inertStates) {
        if (!wasInert) {
          element.removeAttribute("inert");
        }
      }
      if (shouldRestoreFocusRef.current && opener?.isConnected) {
        opener.focus();
      }
      shouldRestoreFocusRef.current = false;
    };
  }, [active, dismiss]);

  return { dialogRef, dismiss, markDismissed };
}
