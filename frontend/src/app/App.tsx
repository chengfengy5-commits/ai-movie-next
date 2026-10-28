import { useState } from "react";
import { AdminFeature } from "../features/admin/AdminFeature";
import { AppearanceProvider } from "../features/appearance/ThemeProvider";
import { PublicSiteConfigProvider } from "../features/site-config/PublicSiteConfigProvider";
import { ApiConfigurationError, resolveApiBaseUrl } from "../shared/api/config";
import { AuthProvider } from "../features/auth/AuthProvider";
import { ResetPasswordPage } from "../features/auth/ResetPasswordPage";
import { Workspace } from "./Workspace";
import {
  createServicesFromEnvironment,
  type WorkspaceServices,
} from "../shared/api/services";

interface AppProps {
  services?: WorkspaceServices;
  resetPasswordTokenHolder?: { current: string | null };
}

type ServiceInitialization =
  | { services: WorkspaceServices; error: null }
  | { services: null; error: string };

function initializeServices(): ServiceInitialization {
  try {
    return { services: createServicesFromEnvironment(), error: null };
  } catch (error) {
    const message = error instanceof ApiConfigurationError
      ? error.message
      : "工作台启动配置无效。";
    return { services: null, error: message };
  }
}

function isAdminPath(pathname: string): boolean {
  return pathname === "/admin" || pathname.startsWith("/admin/");
}

function resolveAdminApiBaseUrl(services: WorkspaceServices | null): string | null {
  if (services === null || services.mode !== "api" || services.apiBaseUrl === null) {
    return null;
  }
  try {
    return resolveApiBaseUrl(services.apiBaseUrl, window.location.href);
  } catch (error) {
    if (error instanceof ApiConfigurationError) return null;
    throw error;
  }
}

function AdminApiRequired() {
  return (
    <main className="admin-api-required-screen">
      <section className="admin-api-required-card" role="status">
        <span className="admin-brand-mark" aria-hidden="true">H</span>
        <p className="admin-eyebrow">Hao AI 管理后台</p>
        <h1>管理后台需要本地 API</h1>
        <p>请启用 API 模式并配置本机 /api 服务后重试。</p>
      </section>
    </main>
  );
}

interface AppRoutesProps {
  services: WorkspaceServices | null;
  initialized: ServiceInitialization;
  resetPasswordTokenHolder: AppProps["resetPasswordTokenHolder"];
}

function AppRoutes({ services, initialized, resetPasswordTokenHolder }: AppRoutesProps) {
  if (window.location.pathname === "/reset-password.html") {
    return (
      <ResetPasswordPage
        accountAccess={services?.accountAccess}
        serviceUnavailableMessage={services === null
          ? initialized.error ?? "本地 API 服务配置不可用，请检查运行环境后重试。"
          : services.mode === "demo"
            ? "当前演示模式不连接密码重置服务。请在配置了本地 API 的环境中重新打开重置链接。"
            : "当前 API 服务未提供密码重置能力，请检查本地服务版本。"}
        onTokenConsumed={() => {
          if (resetPasswordTokenHolder !== undefined) {
            resetPasswordTokenHolder.current = null;
          }
        }}
        token={resetPasswordTokenHolder?.current ?? null}
      />
    );
  }

  if (isAdminPath(window.location.pathname)) {
    const apiBaseUrl = resolveAdminApiBaseUrl(services);
    return apiBaseUrl === null
      ? <AdminApiRequired />
      : <AdminFeature apiBaseUrl={apiBaseUrl} />;
  }

  if (services === null) {
    return (
      <main className="configuration-screen">
        <section className="configuration-card" role="alert">
          <span className="brand-mark" aria-hidden="true">H</span>
          <p className="eyebrow">工作台未启动</p>
          <h1>本地服务配置需要检查</h1>
          <p>{initialized.error ?? "当前运行模式没有对应的服务配置。"}</p>
          <p className="configuration-help">
            演示模式无需配置 API。API 模式请使用回环地址打开页面，并配置本地 /api 地址。
          </p>
        </section>
      </main>
    );
  }

  return (
    <AuthProvider services={services}>
      <Workspace />
    </AuthProvider>
  );
}

function AppContent(props: AppProps) {
  const [initialized] = useState(initializeServices);
  const services = props.services ?? initialized.services;

  return (
    <PublicSiteConfigProvider services={services}>
      <AppRoutes
        initialized={initialized}
        resetPasswordTokenHolder={props.resetPasswordTokenHolder}
        services={services}
      />
    </PublicSiteConfigProvider>
  );
}

export function App(props: AppProps) {
  return (
    <AppearanceProvider>
      <AppContent {...props} />
    </AppearanceProvider>
  );
}
