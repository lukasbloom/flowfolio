"use client";

import { createContext, useCallback, useContext, useMemo, useReducer, useRef, type ReactNode } from "react";

// State shape for the add-transaction flow (yield is one of the form types).
export type AddTxnFormType = "buy" | "sell" | "trade" | "spend" | "yield";

export type AddTxnState =
  | { mode: "idle" }
  | { mode: "picker" }
  | { mode: "form"; type: AddTxnFormType };

export interface AddTxnContextValue {
  state: AddTxnState;
  openTypePicker(): void;
  openForm(type: AddTxnFormType): void;
  close(): void;
  // Provider-owned focus-return target. Any mounted Add-trigger element
  // (desktop AddButton + mobile AddTxnFab) calls registerTrigger(el) on mount
  // so the Picker/FormSheet onCloseAutoFocus handlers can refocus the trigger
  // without depending on the DOM id `add-trigger`. Last-mounted wins — the
  // desktop trigger (hidden md:flex parent) and mobile FAB (md:hidden) cannot
  // be mounted at the same viewport, so single-slot semantics are sufficient.
  triggerRef: React.RefObject<HTMLElement | null>;
  registerTrigger(el: HTMLElement | null): void;
}

type Action =
  | { type: "OPEN_PICKER" }
  | { type: "OPEN_FORM"; formType: AddTxnFormType }
  | { type: "CLOSE" };

function reducer(_state: AddTxnState, action: Action): AddTxnState {
  switch (action.type) {
    case "OPEN_PICKER":
      return { mode: "picker" };
    case "OPEN_FORM":
      return { mode: "form", type: action.formType };
    case "CLOSE":
      return { mode: "idle" };
    default: {
      const _exhaustive: never = action;
      return _exhaustive;
    }
  }
}

const AddTxnContext = createContext<AddTxnContextValue | null>(null);

export function AddTxnProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, { mode: "idle" } as AddTxnState);

  const openTypePicker = useCallback(() => dispatch({ type: "OPEN_PICKER" }), []);
  const openForm = useCallback(
    (type: AddTxnFormType) => dispatch({ type: "OPEN_FORM", formType: type }),
    [],
  );
  const close = useCallback(() => dispatch({ type: "CLOSE" }), []);

  // triggerRef is overwritten on every registerTrigger call. Last-mounted
  // wins; the ref itself is stable, so it is intentionally excluded from the
  // useMemo dependency array below.
  const triggerRef = useRef<HTMLElement | null>(null);
  const registerTrigger = useCallback((el: HTMLElement | null) => {
    triggerRef.current = el;
  }, []);

  const value = useMemo<AddTxnContextValue>(
    () => ({ state, openTypePicker, openForm, close, triggerRef, registerTrigger }),
    [state, openTypePicker, openForm, close, registerTrigger],
  );

  return <AddTxnContext.Provider value={value}>{children}</AddTxnContext.Provider>;
}

export function useAddTxn(): AddTxnContextValue {
  const ctx = useContext(AddTxnContext);
  if (!ctx) throw new Error("useAddTxn must be used inside AddTxnProvider");
  return ctx;
}
