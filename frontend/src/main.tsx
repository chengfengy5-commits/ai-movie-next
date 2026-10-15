import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import "./styles.css";
import { captureResetTokenFromLocation } from "./features/auth/ResetPasswordPage";

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("Missing root element.");
}

const resetPasswordTokenHolder = { current: captureResetTokenFromLocation() };

createRoot(rootElement).render(
  <StrictMode>
    <App resetPasswordTokenHolder={resetPasswordTokenHolder} />
  </StrictMode>,
);
